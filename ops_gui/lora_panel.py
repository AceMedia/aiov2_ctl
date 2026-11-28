"""
The LoRa tab: mesh status, nodes heard, a live activity feed and an opt in message box.

It reads the same Meshtastic engine as the ops console (ops_agent.lora.LoRa) through
meshtasticd on localhost, so there is one backend, shared with other tabs and with notifications
through mesh_hub.py: the connection stays open while the LoRa rail is on and either this tab is
showing or mesh notifications are wanted, not only while this tab is showing. Receiving is the
default; sending has to be switched on in the tab each time, because a message goes out over the air.
The helpers at the top are pure so they can be tested without a radio.
"""
import html
import math
import time

from ops_agent import delivery, geo, nodeid

TICK_MS = 1000


def node_name(info, node_id=""):
    user = (info or {}).get("user") or {}
    return user.get("longName") or user.get("shortName") or str(node_id) or "unknown"


DATED_AFTER_DAYS = 14                # older than this, say the date: "309d ago" made people do sums


def age_text(last_heard, now=None, dated=True):
    """When a node was last heard. The mesh service remembers nodes between runs, with the time it last heard
    each one, so a node can be listed that has not been heard for months. Up to two weeks this is "5m ago",
    "3d ago"; beyond that it is the date ("20 Jan 2026"), which says plainly that it is remembered and not
    heard lately. `dated=False` keeps days however long, for sentences such as "Not heard for 309d"."""
    if not last_heard:
        return "never"
    secs = max(0, int((now or time.time()) - last_heard))
    if secs < 60:
        return "now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = secs // 86400
    if dated and days > DATED_AFTER_DAYS:
        return time.strftime("%d %b %Y", time.localtime(last_heard)).lstrip("0")
    return f"{days}d ago"


def signal_text(snr, rssi=None):
    bits = []
    if snr is not None:
        bits.append(f"{snr:+.1f} dB")
    if rssi is not None:
        bits.append(f"{rssi} dBm")
    return "  ".join(bits) or "-"


CHAT_LIMIT = 300                     # as many as the engine keeps (LoRa.max_chat)


def chat_events(snapshot):
    """The events the Messages view is built from: the engine's own chat list, which telemetry cannot push out,
    or the activity list from an engine too old to keep one."""
    snap = snapshot or {}
    chat = snap.get("chat")
    return chat if isinstance(chat, list) else snap.get("activities") or []


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def link_reading(info):
    """(SNR in dB, RSSI in dBm, when) for the last reading of a node's signal, each None where unknown. The
    engine's own reading of the last packet heard from the node (linkMetrics, which carries RSSI and its time)
    comes first; otherwise the mesh service's SNR, as of when the node was last heard."""
    info = info if isinstance(info, dict) else {}
    link = info.get("linkMetrics") if isinstance(info.get("linkMetrics"), dict) else {}
    snr, rssi = _number(link.get("rxSnr")), _number(link.get("rxRssi"))
    if snr is not None or rssi is not None:
        return snr, rssi, link.get("time")
    snr = _number(info.get("snr"))
    return snr, None, info.get("lastHeard") if snr is not None else None


SIGNAL_HEADER = "SNR / RSSI"


def signal_cell(snr, rssi, when=None, now=None):
    """The figures alone, compact enough for the table: "+6.2 dB  -91 dBm", with "-" in place of a figure that is
    not known and "-" for the whole cell when neither is. The header ("SNR / RSSI") says which is which, and the
    age of the reading is left to the Heard column; signal_tooltip() has the full wording. Never bars, and never a
    distance: signal strength does not say how far away a node is. `when` and `now` are accepted and unused, so
    the call can pass a whole link_reading()."""
    if snr is None and rssi is None:
        return "-"
    return f"{'-' if snr is None else f'{snr:+.1f} dB'}  {'-' if rssi is None else f'{rssi:.0f} dBm'}"


def signal_tooltip(snr, rssi, when=None, now=None):
    """The same reading in full, saying what each figure is and how old it is: "SNR +6.2 dB  RSSI -91 dBm  2m ago"."""
    bits = []
    if snr is not None:
        bits.append(f"SNR {snr:+.1f} dB")
    if rssi is not None:
        bits.append(f"RSSI {rssi:.0f} dBm")
    if not bits:
        return "No signal reading yet."
    if when:
        bits.append(age_text(when, now))
    return "  ".join(bits)


def message_rows(activities, channel=None, limit=CHAT_LIMIT):
    """Text messages and check-ins from the activity list, oldest first, optionally for one channel."""
    rows = []
    for a in activities or []:
        if not isinstance(a, dict) or a.get("kind") not in ("text", "checkin"):
            continue
        if channel is not None and a.get("channel") != channel:
            continue
        rows.append({"time": a.get("time", ""), "who": "Me" if a.get("sent") else a.get("from") or "Unknown",
                     "text": a.get("summary", ""), "channel": a.get("channel", 0), "sent": bool(a.get("sent")),
                     "checkin": a.get("kind") == "checkin",
                     "snr": a.get("rx_snr"), "from_id": a.get("from_id"), "to_id": a.get("to_id"),
                     "packet_id": a.get("packet_id"), "stamp": a.get("stamp"),
                     "state": delivery.delivery_text(a.get("delivery") or {"state": delivery.HANDED})
                     if a.get("sent") else ""})
    return rows[-limit:]


BROADCAST_NUMBER = 0xFFFFFFFF          # the library's broadcast address, written "^all" in its packets


def destination(to_id, self_id=None):
    """Where a packet went: "everyone" (the broadcast address, "^all" or 0xFFFFFFFF), "me" (this radio), "other"
    (another node: this radio only overheard it), or None when the packet did not say."""
    if to_id is None or to_id == "":
        return None
    if str(to_id).strip() == "^all" or nodeid.canonical(to_id) == nodeid.canonical(BROADCAST_NUMBER):
        return "everyone"
    if nodeid.same(to_id, self_id):
        return "me"
    return "other"


def destination_text(to_id, self_id=None, node_info=None, sent=False):
    """The destination in words: "to everyone on the channel", "direct to this radio", "direct to Ann" (sent by
    this radio) or "direct to Ann, overheard". "" when not known."""
    kind = destination(to_id, self_id)
    if kind == "everyone":
        return "to everyone on the channel"
    if kind == "me":
        return "direct to this radio"
    if kind == "other":
        info = None
        for key, value in (node_info or {}).items():
            if nodeid.same(key, to_id):
                info = value
        name = node_name(info, nodeid.canonical(to_id)) if info else nodeid.canonical(to_id)
        return f"direct to {name}" + ("" if sent else ", overheard")
    return ""


def unreadable_text(kind):
    """What to say in place of an encrypted packet's content, which is never shown."""
    if kind == "other":
        return "a direct message to another node; its text is encrypted to them and cannot be read here"
    return "encrypted with a key this radio does not have; its content cannot be read here"


def view_signature(rows):
    """What the Messages view shows, so it is redrawn when a sent message's state changes as well as when a
    message arrives. row_signature() alone stays the identity used for marking what has been read."""
    return tuple((row_signature(r), r.get("state")) for r in rows or [])


def sent_note(ok, want_ack):
    """What the Mesh tab says once Send is pressed. Never just "Sent": that would claim more than is known."""
    if not ok:
        return "Send failed. See the activity feed."
    if want_ack:
        return "Handed to the radio. Waiting for an acknowledgement (see Messages)."
    return "Handed to the radio. Whether anyone received it is not known."


def row_signature(row):
    """A row's identity, stable across refreshes, for marking how far the user has read: the sender (node id when
    known), the radio's packet id when it gave one, the channel and the time (exact when known, else the clock
    text). Without a packet id the text is part of it too. The channel matters: the same words on two channels in
    the same second are two messages."""
    if not row:
        return None
    sender = row.get("from_id") or row.get("who")
    when = row.get("stamp") or row.get("time")
    if row.get("packet_id") is not None:
        return (sender, row["packet_id"], row.get("channel", 0), when)
    return (sender, None, row.get("channel", 0), when, row.get("text"))


def stick_to_bottom(position, maximum, slack=4):
    """Whether a refresh should scroll to the newest message: only when the reader was already at the bottom
    (within `slack` pixels). Someone reading older messages keeps their place."""
    try:
        return position >= maximum - slack
    except TypeError:
        return True


def scroll_after_fill(first_fill, position, maximum, slack=4):
    """Where the Messages view goes after it is filled: "bottom" (the newest message) or "keep" (where the reader
    was). The first time it is filled, and the first time after the channel filter changes, it always goes to the
    newest message: the scroll position then belongs to an empty view or to other messages, and used to leave the
    view at the oldest. After that stick_to_bottom() decides, so someone reading older messages keeps their place."""
    if first_fill:
        return "bottom"
    return "bottom" if stick_to_bottom(position, maximum, slack) else "keep"


def first_fill_done(has_rows, visible):
    """Whether the first fill has really happened. Not while the view is empty, and not while it is hidden behind
    another tab: the view is filled in the background, where it has no size and so no true end to scroll to, and
    the newest message must still be shown when the reader first opens it."""
    return bool(has_rows and visible)


def unread_count(rows, read_mark):
    """Incoming messages after the row last marked as read. Our own messages never count.

    `read_mark` is a row_signature(), not a count: `activities` is capped (message_rows keeps only the newest
    `limit`), so once that cap is reached the *count* of incoming messages stops changing even as new ones arrive
    and old ones fall off the front, and a count-based unread total gets stuck at 0 forever. Tracking identity
    instead keeps counting correctly past the cap; if the marked row has itself scrolled out of the window, every
    incoming row still in it is treated as unread, which is a safer default than staying stuck at 0."""
    if read_mark is None:
        return sum(1 for r in rows if not r["sent"])
    for i, r in enumerate(rows):
        if row_signature(r) == read_mark:
            return sum(1 for r in rows[i + 1:] if not r["sent"])
    return sum(1 for r in rows if not r["sent"])


def message_channels(activities):
    return sorted({a.get("channel", 0) for a in activities or [] if isinstance(a, dict) and a.get("kind") in ("text", "checkin")})


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Compass bearing from the first point to the second, 0 to 360."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def node_latlon(info):
    """(lat, lon) from a node's position, or None. The one checked converter, ops_agent.geo.latlon()."""
    return geo.latlon(info.get("position")) if isinstance(info, dict) else None


def own_position(node_info, self_id):
    """This mesh node's own position, if it has one (a GPS fix or a fixed position).

    Node ids are compared as strings: node_info may key its entries differently from how self_id
    is reported (one an int, the other a hex string), and a direct dict lookup would then always
    miss, same as it would for every other node here (see radar_points, which compares as strings)."""
    if self_id is None or not node_info:
        return None
    for node_id, info in node_info.items():
        if nodeid.same(node_id, self_id):
            return node_latlon(info)
    return None


def distance_text(km):
    if km is None:
        return "-"
    return f"{km * 1000:.0f} m" if km < 1 else f"{km:.1f} km"


def position_time(info):
    """When a node's position is from, in seconds since the epoch, or None when nobody knows: the position's own
    time when it carries one, otherwise when this app received it (the engine's "positionReceived"). Never when the
    node was last heard: a node heard a minute ago may be sending nothing but a position from hours ago."""
    info = info if isinstance(info, dict) else {}
    position = info.get("position") if isinstance(info.get("position"), dict) else {}
    for value in (position.get("time"), info.get("positionReceived")):
        value = _number(value)
        if value and value > 0:
            return value / 1000.0 if value > 1_000_000_000_000 else value
    return None


def position_age_text(when, now=None):
    """The age of a position for the radar: "position 2h ago", or "position of unknown age"."""
    return f"position {age_text(when, now)}" if when else "position of unknown age"


def radar_tooltip(points):
    """One line per node on the radar: name, distance and how old its position is."""
    return "\n".join(f"{p['name']}: {distance_text(p['km'])}, {p['position_age']}" for p in points or []) \
        or "No other node has a position."


def radar_points(node_info, self_id, own, radius_km, now=None, stale_after=900):
    """Nodes with a position as plot coordinates around `own`, plus the range plotted in km.

    x runs east and y runs north, both scaled so the edge of the plot is 1. The range is twice the
    proximity ring or the farthest node, whichever is larger, so the ring is always visible.
    A dot is stale when its POSITION is older than `stale_after`, or of unknown age, however recently the node
    was heard (position_time()).
    """
    now = now or time.time()
    found = []
    if own:
        for node_id, info in (node_info or {}).items():
            if nodeid.same(node_id, self_id):
                continue
            where = node_latlon(info)
            if not where:
                continue
            km = haversine_km(own[0], own[1], where[0], where[1])
            fixed = position_time(info)
            found.append({"id": str(node_id), "name": node_name(info, node_id), "km": km,
                          "bearing": bearing_deg(own[0], own[1], where[0], where[1]),
                          "inside": km <= radius_km,
                          "position_time": fixed,
                          "position_age": position_age_text(fixed, now),
                          "stale": fixed is None or now - fixed >= stale_after})
    span = max([radius_km * 2] + [p["km"] * 1.1 for p in found]) or 1.0
    for p in found:
        rad = math.radians(p["bearing"])
        p["x"], p["y"] = p["km"] / span * math.sin(rad), p["km"] / span * math.cos(rad)
    found.sort(key=lambda p: p["km"])
    return found, span


def channel_label(channel):
    """ "0: LongFast (primary)", or "0: default name (primary)" when the primary's name is empty."""
    name = channel.get("name") or ("default name" if channel.get("primary") else f"channel {channel.get('index', 0)}")
    return f"{channel.get('index', 0)}: {name}" + (" (primary)" if channel.get("primary") else "")


def channel_choices(snapshot):
    """[(channel number, label)] for the "Send on" list. The channels set up on the radio when the connection gives
    them (configured_channels in the snapshot: the primary first, disabled ones already left out); otherwise, as
    before, the channels with recent activity. The selected channel is always listed, and said not to be set up
    when the radio's list does not have it."""
    snap = snapshot or {}
    configured = [c for c in snap.get("configured_channels") or [] if isinstance(c, dict)]
    sel = snap.get("selected_channel")
    if configured:
        out = [(c.get("index", 0), channel_label(c)) for c in configured]
        if sel is not None and sel not in [n for n, _ in out]:
            out.append((sel, f"Channel {sel} (not set up on this radio)"))
        return out
    items = {}
    for c in snap.get("active_channels") or []:
        items[c["channel"]] = f"Channel {c['channel']}  ({c.get('count', 0)} packets)"
    sel = snap.get("selected_channel")
    if sel is not None and sel not in items:
        items[sel] = f"Channel {sel}"
    if not items:
        items[0] = "Channel 0"
    return sorted(items.items())


def alert_lines(recent_alerts, stale, node_info=None):
    """Newest first: proximity alerts, then nodes not heard for a while."""
    lines = [f"{a.get('time', '')}  {a.get('message', '')}" for a in reversed(recent_alerts or [])]
    for node_id, age in (stale or [])[:5]:
        name = node_name((node_info or {}).get(node_id), node_id)
        lines.append(f"Not heard for {age_text(time.time() - age, dated=False).replace(' ago', '')}: {name}")
    return lines


def _row_distance(info, node_id, self_id, own):
    where = node_latlon(info)
    if not own or not where or nodeid.same(node_id, self_id):
        return "-"
    return distance_text(haversine_km(own[0], own[1], where[0], where[1]))


NAME_MIN_PX = 160                    # the name column never gets less than this, so names stay readable


def column_widths(total, needs, stretch=0, minimum=NAME_MIN_PX):
    """Pixel widths for the node table's columns. Every column but `stretch` (the name) gets what its header and
    cells need, and the name gets all the rest, never less than `minimum`. When the table is too narrow for that,
    the other columns give up width in proportion to what they need, so the name is the last to be cut."""
    total = max(0, int(total))
    needs = [max(0, int(n)) for n in needs]
    if not needs:
        return []
    others = sum(n for i, n in enumerate(needs) if i != stretch)
    if total - others >= minimum:
        widths = list(needs)
    else:
        room = max(0, total - minimum)
        scale = room / others if others else 0.0
        widths = [int(n * scale) for n in needs]
    widths[stretch] = max(0, total - sum(w for i, w in enumerate(widths) if i != stretch))
    return widths


def node_rows(node_info, self_id=None, now=None, own=None):
    """Rows for the node table: this node first, then the rest by when they were last heard."""
    rows = []
    for node_id, info in (node_info or {}).items():
        if not isinstance(info, dict):
            continue
        metrics = info.get("deviceMetrics") or {}
        battery = metrics.get("batteryLevel")
        hops = info.get("hopsAway")
        reading = link_reading(info)
        rows.append({
            "id": str(node_id),
            "name": node_name(info, node_id),
            "signal": signal_cell(*reading, now=now),
            "signal_tip": signal_tooltip(*reading, now=now),
            "heard": age_text(info.get("lastHeard"), now),
            "battery": "-" if battery is None else ("plugged in" if battery > 100 else f"{battery}%"),
            "hops": "-" if hops is None else str(hops),
            "distance": _row_distance(info, node_id, self_id, own),
            "self": nodeid.same(node_id, self_id),
            "_heard": info.get("lastHeard") or 0,
        })
    rows.sort(key=lambda r: (not r["self"], -r["_heard"]))
    return rows


NODE_COLUMNS = ("name", "signal", "distance", "heard", "battery", "hops")

DETAILS_PROMPT = "Move to a node in the list with the arrow keys or the D-pad to see its details."


def right_card(picked, info):
    """What the right-hand card of the Mesh sub-tab shows, as (title, "details" or "activity"): the chosen node's
    details while a node is chosen and its record is there, otherwise Activity as before. The nodes table keeps its
    full height either way."""
    if picked and isinstance(info, dict):
        return "Node details", "details"
    return "Activity", "activity"


def pick_again(picked, chosen):
    """The node chosen after a row is chosen: choosing the row already chosen clears the choice (back to Activity),
    any other row chooses that node."""
    return None if chosen is not None and chosen == picked else chosen


def escape_clears(picked):
    """Whether Escape in the nodes table clears the choice. Only while a node is chosen: otherwise Escape is left
    to the window's own Escape/Back handling."""
    return bool(picked)


def _when(value):
    """A time in seconds since the epoch from a number the library or the engine gave, or None."""
    value = _number(value)
    if not value or value <= 0:
        return None
    return value / 1000.0 if value > 1_000_000_000_000 else value


def precision_text(bits):
    """What the precision a node sent its position with means, or None when it did not say. Meshtastic rounds a
    shared position to `bits` bits; 32 is the exact position. The distances follow Meshtastic's own table (10 bits is
    about 23.3 km, and each further bit halves it): an assumption from its documentation, not checked on a radio."""
    bits = _number(bits)
    if bits is None or bits <= 0:
        return None
    bits = int(bits)
    if bits >= 32:
        return "sent at full precision"
    return f"sent rounded to {bits} bits, so only to within about {distance_text(23.3 / 2 ** (bits - 10))}"


def _pct(value):
    return f"{value:.0f}%" if value >= 10 else f"{value:.1f}%"


def node_details(info, node_id, now=None, self_id=None):
    """The lines of the details block for one node, from its record as the engine keeps it. Only what the node
    reported is shown: anything missing is left out, never shown as zero. A position is shown only when the node sent
    one, with its own age and the precision it was sent with; nothing here is worked out from signal strength."""
    info = info if isinstance(info, dict) else {}
    now = now or time.time()
    user = info.get("user") if isinstance(info.get("user"), dict) else {}
    lines = []
    if user.get("longName"):
        lines.append(f"Name: {user['longName']}")
    if user.get("shortName"):
        lines.append(f"Short name: {user['shortName']}")
    ident = nodeid.canonical(user.get("id") or node_id)
    if ident:
        lines.append(f"Identity: {ident}" + (" (this radio)" if nodeid.same(node_id, self_id) else ""))
    if user.get("hwModel") and str(user["hwModel"]) != "UNSET":
        lines.append(f"Hardware: {user['hwModel']}")
    if user.get("role"):
        lines.append(f"Role: {user['role']}")
    heard = _when(info.get("lastHeard"))
    if heard:
        lines.append(f"Last heard: {age_text(heard, now)}")
    snr, rssi, when = link_reading(info)
    if snr is not None or rssi is not None:
        bits = ([f"SNR {snr:+.1f} dB"] if snr is not None else []) + ([f"RSSI {rssi:.0f} dBm"] if rssi is not None else [])
        when = _when(when)
        lines.append("Signal heard here: " + "  ".join(bits) + f" ({age_text(when, now) if when else 'age not known'})")
    hops = _number(info.get("hopsAway"))
    if hops is not None:
        lines.append(f"Hops away: {hops:.0f}" + (" (heard directly)" if hops == 0 else ""))
    metrics = info.get("deviceMetrics") if isinstance(info.get("deviceMetrics"), dict) else {}
    battery, voltage = _number(metrics.get("batteryLevel")), _number(metrics.get("voltage"))
    if battery is not None:
        lines.append("Power: on external power" if battery > 100 else f"Battery: {battery:.0f}%")
    if voltage is not None and voltage > 0:
        lines.append(f"Voltage: {voltage:.2f} V")
    use, air = _number(metrics.get("channelUtilization")), _number(metrics.get("airUtilTx"))
    if use is not None or air is not None:
        got = _when(info.get("telemetryReceived"))
        bits = ([f"channel {_pct(use)} busy"] if use is not None else []) + \
               ([f"transmitting {_pct(air)} of the time"] if air is not None else [])
        lines.append("Its own report: " + ", ".join(bits) +
                     f" ({age_text(got, now) if got else 'time of the report not known'})")
    where = node_latlon(info)
    if where:
        position = info.get("position") if isinstance(info.get("position"), dict) else {}
        alt = _number(position.get("altitude"))
        text = f"Position it sent: {where[0]:.5f}, {where[1]:.5f}" + (f", {alt:.0f} m up" if alt is not None else "")
        lines.append(f"{text} ({position_age_text(position_time(info), now)})")
        precision = precision_text(position.get("precisionBits"))
        if precision:
            lines.append(f"Position precision: {precision}")
    return lines


def channel_busy_text(node_info, self_id, now=None):
    """How busy the channel is, as this radio's own report of itself (its device telemetry: channelUtilization and
    airUtilTx), with the report's age: "This radio reports the channel 12% busy and has been transmitting 0.4% of the
    time, as of 2m ago". "-" when it has not reported them."""
    info = None
    if self_id is not None:
        for key, value in (node_info or {}).items():
            if nodeid.same(key, self_id) and isinstance(value, dict):
                info = value
    metrics = (info or {}).get("deviceMetrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    use, air = _number(metrics.get("channelUtilization")), _number(metrics.get("airUtilTx"))
    if use is None and air is None:
        return "-"
    bits = []
    if use is not None:
        bits.append(f"the channel {_pct(use)} busy")
    if air is not None:
        bits.append(f"{'has been' if use is not None else 'it has been'} transmitting {_pct(air)} of the time")
    text = "This radio reports " + " and ".join(bits) if use is not None else \
        "This radio reports that " + bits[0]
    got = _when(info.get("telemetryReceived"))
    if got:
        age = age_text(got, now)
        return text + (", as of just now" if age == "now" else f", as of {age}")
    return text + " (when it reported this is not known)"


SPARK_RANGES = {"battery": (0.0, 100.0), "snr": (-20.0, 12.0)}   # what each small chart spans, top to bottom


def series_values(points):
    """The values of a node's history, oldest first, for pixel.spark_columns()."""
    return [p[1] for p in points or [] if isinstance(p, (list, tuple)) and len(p) == 2]


def series_caption(name, points, now=None):
    """The words beside a small chart: "Battery heard here, 12 readings: 80% to 76%, latest 2m ago"."""
    label, fmt = {"battery": ("Battery", lambda v: f"{v:.0f}%"),
                  "snr": ("SNR", lambda v: f"{v:+.1f} dB")}[name]
    values = series_values(points)
    if not values:
        return f"{label}: no readings heard here yet"
    latest = age_text(points[-1][0], now)
    if len(values) == 1:
        return f"{label} heard here, 1 reading: {fmt(values[0])}, {latest}"
    return f"{label} heard here, {len(values)} readings: {fmt(values[0])} to {fmt(values[-1])}, latest {latest}"


def picked_node(row_ids, current_row):
    """The node chosen in the table: the id on the current row, or None when no row is current."""
    if isinstance(current_row, int) and 0 <= current_row < len(row_ids or []):
        return row_ids[current_row]
    return None


def row_of(row_ids, node_id):
    """The row a chosen node is on after the table is rebuilt, or -1 when it is no longer listed."""
    try:
        return list(row_ids or []).index(node_id)
    except ValueError:
        return -1


def rows_signature(rows):
    """What the nodes table shows, as a tuple, so a refresh can tell whether anything changed at all."""
    return tuple(tuple(r.get(k) for k in NODE_COLUMNS) + (r.get("signal_tip"), bool(r.get("self")))
                 for r in rows or [])


def activity_line(a, self_id=None, node_info=None):
    """One line of the Activity feed. When the packet said where it went, the line says so; an encrypted packet's
    content is never shown, only what it was."""
    sig = ""
    where = destination_text(a.get("to_id"), self_id, node_info, sent=bool(a.get("sent")))
    summary = a.get("summary", "")
    if a.get("kind") == "encrypted":
        summary = unreadable_text(destination(a.get("to_id"), self_id))
    if a.get("rx_snr") is not None:
        sig = f"  ({a['rx_snr']:+.1f} dB)"
    arrow = ">" if a.get("sent") else "<"
    if a.get("sent") and isinstance(a.get("delivery"), dict):
        sig = f"  [{delivery.delivery_text(a['delivery'])}]"
    who = f"{a.get('from', '?')} ({where})" if where else a.get("from", "?")
    return f"{a.get('time', '')}  ch{a.get('channel', 0)}  {arrow} {who}: {summary}{sig}"


KEY_WARNINGS = {
    "none": "This channel is not encrypted: anyone with a Meshtastic radio nearby can read what is sent on it.",
    "default": "This channel uses Meshtastic's public default key: anyone with a Meshtastic radio nearby can read "
               "what is sent on it.",
    "public_variant": "This channel uses a simple variant of Meshtastic's public default key, which is just as easy "
                      "to read as the default.",
}


def key_line(snapshot):
    """The standing warning by the message box when anyone nearby can read the channel being sent on (not
    encrypted, the public default key, or a simple variant of it), else "". Nothing is said for a private key, nor
    when the radio's channel list is not available or the key cannot be told: no guessing."""
    snap = snapshot or {}
    sel = snap.get("selected_channel") or 0
    for c in snap.get("configured_channels") or []:
        if isinstance(c, dict) and c.get("index", 0) == sel:
            return KEY_WARNINGS.get(c.get("key"), "")
    return ""


def composer_line(snapshot):
    """Where the message being typed will go. This app only sends to everyone on the chosen channel: "To everyone
    on channel 0 (LongFast)". The channel's own name, or for a primary with an empty name the radio's preset; the
    name is left out when neither is known."""
    snap = snapshot or {}
    sel = snap.get("selected_channel") or 0
    name = ""
    for c in snap.get("configured_channels") or []:
        if isinstance(c, dict) and c.get("index", 0) == sel:
            name = c.get("name") or (snap.get("modem_preset") or "" if c.get("primary") else "")
    return f"To everyone on channel {sel}" + (f" ({name})" if name else "")


def hint(library_ok, service_active, rail):
    """The next step when the mesh is not reachable, in plain words."""
    if not library_ok:
        return "The meshtastic Python library is not installed: pip3 install --break-system-packages meshtastic"
    if rail != "on":
        return "The LoRa rail is off."
    if not service_active:
        return "meshtasticd is stopped. Turning the LoRa rail off and on again starts it, or run: sudo systemctl start meshtasticd"
    return ""


def can_send(enabled, connected, text):
    return bool(enabled and connected and (text or "").strip())


def build_panel(win, Switch):
    """Return (widget, activate, deactivate)."""
    import subprocess
    import threading

    from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, QObject, pyqtSignal
    from PyQt6.QtGui import QColor, QPainter, QPalette, QPen
    from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                                 QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
                                 QTabWidget, QTextBrowser, QVBoxLayout, QWidget)
    from .foldertabs import FolderTabWidget
    from . import pixel

    class Signals(QObject):
        ready = pyqtSignal(object)

    class Radar(QWidget):
        """Nodes around this one by bearing and distance, with the proximity ring."""

        def __init__(self):
            super().__init__()
            self.setMinimumSize(240, 200)
            self.points, self.span, self.radius, self.waiting = [], 1.0, 1.0, True

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            text = self.palette().color(QPalette.ColorRole.WindowText)
            side = min(self.width(), self.height()) - 36
            cx, cy, r = self.width() / 2, self.height() / 2, side / 2
            ring = QColor(text)
            ring.setAlpha(70)
            p.setPen(QPen(ring, 1))
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
            p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
            if self.span > 0:
                rr = r * self.radius / self.span
                p.setPen(QPen(QColor("#37b24d"), 2))
                p.drawEllipse(QPointF(cx, cy), rr, rr)
            p.setPen(text)
            for label, dx, dy in (("N", 0, -1), ("E", 1, 0), ("S", 0, 1), ("W", -1, 0)):
                p.drawText(QRectF(cx + dx * (r + 16) - 10, cy + dy * (r + 16) - 10, 20, 20),
                           Qt.AlignmentFlag.AlignCenter, label)
            if self.waiting:
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                           "Waiting for this node's position")
                p.end()
                return
            p.setPen(QPen(QColor("#111111"), 1))
            p.setBrush(QColor("#2e9bff"))
            p.drawEllipse(QPointF(cx, cy), 6, 6)
            for n in self.points:
                p.setBrush(QColor("#868e96") if n["stale"] else QColor("#37b24d") if n["inside"]
                           else QColor("#f59f00"))
                x, y = cx + n["x"] * r, cy - n["y"] * r
                p.drawEllipse(QPointF(x, y), 5, 5)
                p.setPen(text)
                p.drawText(QPointF(x + 8, y + 4), f"{n['name'][:14]}  {age_text(n['position_time'])}"
                           if n.get("position_time") else n["name"][:14])
                p.setPen(QPen(QColor("#111111"), 1))
            p.setPen(text)
            p.drawText(QPointF(10, self.height() - 8), f"Edge of the plot: {distance_text(self.span)}")
            p.end()

    class Spark(QWidget):
        """A small pixel line chart of one figure over its last readings, drawn as the Home screen draws its own
        (pixel.spark_columns)."""

        def __init__(self, name):
            super().__init__()
            self.name, self.values = name, []
            self.setFixedHeight(26)

        def paintEvent(self, _):
            lo, hi = SPARK_RANGES[self.name]
            p = QPainter(self)
            colour = QColor("#3fe0d0" if self.name == "snr" else "#37b24d")
            prev = None
            for x, y in pixel.spark_columns(self.values, lo, hi, self.width(), self.height()):
                p.fillRect(x, y, 4, 2, colour)
                if prev is not None and prev != y:
                    p.fillRect(x, min(prev, y), 2, abs(prev - y) + 2, colour)
                prev = y
            p.end()

    class NodeTable(QTableWidget):
        """The nodes table. The name column takes the spare width and the others only what they need
        (see column_widths); Qt's own stretch mode let content-sized columns squeeze names to a few letters.
        Escape clears a chosen node, and Enter on the chosen row does too; with nothing chosen Escape is not taken,
        so the window's own Escape/Back still works."""
        on_escape = on_enter = None
        is_picked = staticmethod(lambda: False)

        def event(self, event):
            from PyQt6.QtCore import QEvent
            if event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape \
                    and escape_clears(self.is_picked()):
                event.accept()                   # take Escape before the window's shortcut does
                return True
            return super().event(event)

        def keyPressEvent(self, event):
            if event.key() == Qt.Key.Key_Escape and escape_clears(self.is_picked()) and self.on_escape:
                self.on_escape()
                return
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.on_enter:
                self.on_enter()
                return
            super().keyPressEvent(event)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self.fit_columns()

        def fit_columns(self):
            head = self.horizontalHeader()
            needs = [0] + [max(self.sizeHintForColumn(c), head.sectionSizeHint(c)) + 12
                           for c in range(1, self.columnCount())]
            for c, w in enumerate(column_widths(self.viewport().width(), needs)):
                head.resizeSection(c, w)

    class MeshPage(QWidget):
        def __init__(self):
            super().__init__()
            self.on_snap = None
            self.lora = None
            self.rows_sig = None
            self.starting = False
            self.signals = Signals()
            self.signals.ready.connect(self.started)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            root = QVBoxLayout(self)

            head = QFrame()
            head.setObjectName("card")
            hl = QVBoxLayout(head)
            self.status = QLabel("")
            self.status.setStyleSheet("font-size: 16pt; font-weight: bold;")
            self.status.setWordWrap(True)
            self.note = QLabel("")
            self.note.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
            self.note.setWordWrap(True)
            self.busy = QLabel("Channel use: -")
            self.busy.setStyleSheet("font-size: 12pt;")
            self.busy.setWordWrap(True)
            hl.addWidget(self.status)
            hl.addWidget(self.busy)
            hl.addWidget(self.note)
            root.addWidget(head)

            chans = QHBoxLayout()
            chans.addWidget(QLabel("Send on"))
            self.channel = QComboBox()
            self.channel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.channel.activated.connect(self.pick_channel)
            chans.addWidget(self.channel, 2)
            self.best_b = QPushButton("Best")
            self.best_b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.best_b.clicked.connect(self.best_channel)
            chans.addWidget(self.best_b)
            self.follow = Switch()
            self.follow.clicked.connect(self.toggle_follow)
            self.lock = Switch()
            self.lock.clicked.connect(self.toggle_lock)
            chans.addWidget(QLabel("Follow"))
            chans.addWidget(self.follow)
            chans.addWidget(QLabel("Lock"))
            chans.addWidget(self.lock)
            chans.addStretch(1)
            self.export_b = QPushButton("Export history")
            self.export_b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.export_b.clicked.connect(self.export)
            chans.addWidget(self.export_b)
            root.addLayout(chans)

            mid = QHBoxLayout()
            nodes = QFrame()
            nodes.setObjectName("card")
            nl = QVBoxLayout(nodes)
            cap = QLabel("Nodes heard")
            cap.setStyleSheet("font-size: 13pt; color: #9e9e9e;")
            self.table = NodeTable(0, 6)
            self.table.setHorizontalHeaderLabels(["Name", SIGNAL_HEADER, "Distance", "Heard", "Battery", "Hops"])
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            # a row can be chosen, as in the other tables of the app: the arrow keys and the D-pad move the current row
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.itemSelectionChanged.connect(self.pick_node)
            self.table.cellClicked.connect(lambda *_: self.clicked_row())
            self.table.on_escape = self.clear_pick
            self.table.on_enter = self.chosen_again
            self.table.is_picked = lambda: self.picked
            self.just_picked = False
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setStretchLastSection(False)
            self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
            self.picked, self.row_ids, self.last_snap = None, [], {}
            self.details = QLabel(DETAILS_PROMPT)
            self.details.setWordWrap(True)
            self.details.setStyleSheet("font-size: 11pt;")
            self.details.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            nl.addWidget(cap)
            nl.addWidget(self.table, 1)
            detail_box = QWidget()
            dl = QVBoxLayout(detail_box)
            dl.setContentsMargins(0, 0, 0, 0)
            dl.addWidget(self.details)
            self.sparks = {}
            for name in ("battery", "snr"):
                caption = QLabel("")
                caption.setStyleSheet("font-size: 10pt; color: #9e9e9e;")
                chart = Spark(name)
                self.sparks[name] = (caption, chart)
                dl.addWidget(caption)
                dl.addWidget(chart)
                caption.hide()
                chart.hide()
            mid.addWidget(nodes, 3)          # the nodes table gets the larger share: names need the room
            feed = QFrame()
            feed.setObjectName("card")
            fl = QVBoxLayout(feed)
            self.feed_cap = QLabel("Activity")
            self.feed_cap.setStyleSheet("font-size: 13pt; color: #9e9e9e;")
            self.feed = QPlainTextEdit()
            self.feed.setReadOnly(True)
            self.feed.setStyleSheet("font-size: 11pt;")
            dl.addStretch(1)
            self.detail_scroll = QScrollArea()          # the chosen node's details, in the place of Activity
            self.detail_scroll.setWidgetResizable(True)
            self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.detail_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.detail_scroll.setWidget(detail_box)
            self.detail_scroll.hide()
            fl.addWidget(self.feed_cap)
            fl.addWidget(self.feed, 1)
            fl.addWidget(self.detail_scroll, 1)
            mid.addWidget(feed, 2)
            root.addLayout(mid, 1)

            self.where = QLabel("")
            self.where.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
            self.where.setWordWrap(True)
            root.addWidget(self.where)
            self.keywarn = QLabel("")                    # a quiet standing line, never a pop-up
            self.keywarn.setStyleSheet("font-size: 11pt; color: #f59f00;")
            self.keywarn.setWordWrap(True)
            self.keywarn.hide()
            root.addWidget(self.keywarn)
            row = QHBoxLayout()
            self.allow = Switch()
            self.allow.clicked.connect(self.toggle_allow)
            self.allow_on = False
            row.addWidget(QLabel("Allow sending"))
            row.addWidget(self.allow)
            self.entry = QLineEdit()
            self.entry.setPlaceholderText("Message to the mesh (turn on Allow sending first)")
            self.entry.setStyleSheet("font-size: 13pt; padding: 6px;")
            self.entry.textChanged.connect(self.update_send)
            self.entry.returnPressed.connect(self.send)
            self.send_b = QPushButton("Send")
            self.send_b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.send_b.clicked.connect(self.send)
            row.addWidget(self.entry, 1)
            row.addWidget(self.send_b)
            root.addLayout(row)
            self.update_send()

        # ---- lifecycle
        def activate(self):
            """Ask the shared mesh connection (see mesh_hub.py) for the engine; it may already be open."""
            if self.lora or self.starting:
                return
            self.starting = True
            self.note_until = 0.0
            self.status.setText("Checking the mesh...")
            win.mesh_hub.acquire("tab", self.signals.ready.emit)

        def started(self, result):
            self.starting = False
            self.note.setText(result["hint"])
            engine = result["lora"]
            if engine is None:
                # no connection, or one that has just been dropped: nothing here may go on using the old one
                self.timer.stop()
                self.lora = None
                self.allow_on = False
                self.allow.show_state(False)
                self.update_send()
                self.show_note(result["hint"])
                self.status.setText("Mesh not reachable")
                return
            self.lora = engine
            self.timer.start(TICK_MS)
            self.tick()

        def deactivate(self):
            self.timer.stop()
            self.lora = None
            self.starting = False
            self.allow_on = False
            self.allow.show_state(False)
            self.update_send()
            win.mesh_hub.release("tab")          # the hub closes the connection unless notifications still want it

        def show_note(self, text, hold_s=4.0):
            """A status message (Sent, Exported to ..., a hint) that tick() leaves alone for a few seconds."""
            self.note.setText(text)
            self.note_until = time.time() + hold_s

        # ---- running
        def tick(self):
            e = self.lora
            if not e:
                return
            e.refresh_nodes()
            snap = e.snapshot()
            self.status.setText(e.get_status())
            tx = snap.get("last_tx_status")
            if time.time() >= self.note_until:          # hold a just-set status (Sent, Exported to...) a moment,
                self.note.setText(f"Sending goes to channel {snap.get('selected_channel', 0)}." +  # rather than
                                  (f"  Last send: {tx}" if tx else ""))                             # overwrite it at once
            self.busy.setText("Channel use: " + channel_busy_text(snap["node_info"], snap.get("self_id")))
            own = own_position(snap["node_info"], snap.get("self_id"))
            rows = node_rows(snap["node_info"], snap.get("self_id"), own=own)
            sig = rows_signature(rows)
            if sig != self.rows_sig:                     # rebuild only when what is shown has changed
                self.rows_sig = sig
                self.table.setRowCount(len(rows))
                for r, row in enumerate(rows):
                    for c, key in enumerate(NODE_COLUMNS):
                        text = row[key] + (" (this node)" if key == "name" and row["self"] else "")
                        item = QTableWidgetItem(text)
                        if key == "signal":
                            item.setToolTip(row["signal_tip"])
                        self.table.setItem(r, c, item)
                self.row_ids = [row["id"] for row in rows]
                keep = row_of(self.row_ids, self.picked)
                if keep >= 0:                            # the chosen node stays chosen when the table is rebuilt
                    self.table.blockSignals(True)
                    self.table.selectRow(keep)
                    self.table.blockSignals(False)
                self.table.fit_columns()
            self.last_snap = snap
            self.show_details()
            lines = [activity_line(a, snap.get("self_id"), snap.get("node_info")) for a in snap["activities"][-40:]]
            self.feed.setPlainText("\n".join(reversed(lines)))
            self.sync_channels(snap)
            self.where.setText(composer_line(snap))
            warning = key_line(snap)
            self.keywarn.setText(warning)
            self.keywarn.setVisible(bool(warning))
            if own:
                e.update_proximity({"lat": own[0], "lon": own[1]}, haversine_km)
            if self.on_snap:
                self.on_snap(e, snap, own)
            self.update_send()

        # ---- the chosen node
        def pick_node(self):
            chosen = picked_node(self.row_ids, self.table.currentRow())
            if chosen is not None and chosen != self.picked:
                self.picked, self.just_picked = chosen, True
            self.show_details()

        def clicked_row(self):
            """A click on a row: the press that chose it is not a second choice; a click on the chosen row is."""
            if self.just_picked:
                self.just_picked = False
                return
            self.chosen_again()

        def chosen_again(self):
            self.just_picked = False
            self.picked = pick_again(self.picked, picked_node(self.row_ids, self.table.currentRow()))
            if self.picked is None:
                self.clear_pick()
            else:
                self.show_details()

        def clear_pick(self):
            """Back to Activity, the way the other tables clear a choice: the selection is cleared quietly."""
            self.picked, self.just_picked = None, False
            self.table.blockSignals(True)
            self.table.clearSelection()
            self.table.blockSignals(False)
            self.show_details()

        def show_details(self):
            snap = self.last_snap or {}
            info = (snap.get("node_info") or {}).get(self.picked) if self.picked else None
            text = "\n".join(node_details(info, self.picked, self_id=snap.get("self_id"))) if info else DETAILS_PROMPT
            title, shows = right_card(self.picked, info)
            self.feed_cap.setText(title)
            self.feed.setVisible(shows == "activity")
            self.detail_scroll.setVisible(shows == "details")
            if text != self.details.text():
                self.details.setText(text)
            series = ((snap.get("node_series") or {}).get(self.picked) or {}) if info else None
            for name, (caption, chart) in self.sparks.items():
                points = (series or {}).get(name) or []
                caption.setVisible(series is not None)
                caption.setText(series_caption(name, points) if series is not None else "")
                chart.setVisible(len(points) >= 2)
                values = series_values(points)
                if values != chart.values:
                    chart.values = values
                    chart.update()

        # ---- channels and export
        def sync_channels(self, snap):
            choices = channel_choices(snap)
            current = [(self.channel.itemData(i), self.channel.itemText(i)) for i in range(self.channel.count())]
            if current != choices:
                self.channel.blockSignals(True)
                self.channel.clear()
                for number, label in choices:
                    self.channel.addItem(label, number)
                self.channel.blockSignals(False)
            idx = self.channel.findData(snap.get("selected_channel"))
            if idx >= 0 and idx != self.channel.currentIndex():
                self.channel.setCurrentIndex(idx)
            self.follow.show_state(bool(snap.get("auto_follow_channel")))
            self.lock.show_state(bool(snap.get("channel_locked")))

        def pick_channel(self, _=0):
            if self.lora and self.channel.currentData() is not None:
                self.lora.set_selected_channel(self.channel.currentData())
                self.tick()

        def best_channel(self):
            if self.lora:
                self.lora.jump_to_best_channel()
                self.tick()

        def toggle_follow(self):
            if self.lora:
                self.lora.toggle_auto_follow_channel()
                self.tick()

        def toggle_lock(self):
            if self.lora:
                self.lora.toggle_channel_lock()
                self.tick()

        def export(self):
            if not self.lora:
                return
            from ops_agent.preferences import export_telemetry_snapshot
            snap = self.lora.snapshot()
            own = own_position(snap["node_info"], snap.get("self_id"))
            path = export_telemetry_snapshot(snap, {"lat": own[0], "lon": own[1]} if own else None)
            self.show_note(f"Exported to {path}" if path else "Export failed.")

        # ---- sending
        def toggle_allow(self):
            self.allow_on = not self.allow_on
            self.allow.show_state(self.allow_on)
            self.update_send()

        def update_send(self, *_):
            self.send_b.setEnabled(can_send(self.allow_on, bool(self.lora), self.entry.text()))
            self.entry.setEnabled(self.allow_on and bool(self.lora))

        def send(self):
            text = self.entry.text().strip()
            if not can_send(self.allow_on, bool(self.lora), text):
                return
            want_ack = delivery.want_ack_setting(win.flag("mesh_want_ack") if hasattr(win, "flag") else None)
            ok = self.lora.send_message(text, want_ack=want_ack)
            self.show_note(sent_note(ok, want_ack))
            if ok:
                self.entry.clear()
            self.tick()

    class MessagesPage(QWidget):
        """Every text message and check-in heard or sent, newest at the bottom, with a channel filter."""

        def __init__(self):
            super().__init__()
            self.read_mark, self.last_sig, self.rows = None, None, []
            self.first_fill = True               # the next fill shows the newest message (scroll_after_fill)
            root = QVBoxLayout(self)
            bar = QHBoxLayout()
            self.count = QLabel("No messages yet.")
            self.count.setStyleSheet("font-size: 16pt; font-weight: bold;")
            self.channel = QComboBox()
            self.channel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.channel.addItem("All channels", None)
            self.channel.activated.connect(self.filter_changed)
            reply = QPushButton("Reply on the Mesh tab")
            reply.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            reply.clicked.connect(self.reply)
            bar.addWidget(self.count, 1)
            bar.addWidget(self.channel)
            bar.addWidget(reply)
            root.addLayout(bar)
            box = QFrame()
            box.setObjectName("card")
            bl = QVBoxLayout(box)
            self.view = QTextBrowser()
            self.view.setStyleSheet("font-size: 13pt;")
            bl.addWidget(self.view)
            root.addWidget(box, 1)
            self.on_reply = None

        def reply(self):
            if self.on_reply:
                self.on_reply()

        def filter_changed(self, _=0):
            self.last_sig, self.first_fill = None, True

        def shown(self):
            """The reader has opened this view: if it has not yet been seen filled, show the newest message."""
            if self.first_fill and self.rows:
                bar = self.view.verticalScrollBar()
                QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
                self.first_fill = False

        def update_from(self, snap):
            acts = chat_events(snap)
            chans = message_channels(acts)
            have = [self.channel.itemData(i) for i in range(1, self.channel.count())]
            if have != chans:
                keep = self.channel.currentData()
                self.channel.blockSignals(True)
                self.channel.clear()
                self.channel.addItem("All channels", None)
                for c in chans:
                    self.channel.addItem(f"Channel {c}", c)
                idx = self.channel.findData(keep)
                self.channel.setCurrentIndex(max(0, idx))
                self.channel.blockSignals(False)
            self.rows = message_rows(acts, self.channel.currentData())
            sig = view_signature(self.rows)
            if sig == self.last_sig:
                return
            self.last_sig = sig
            html_rows = []
            for r in self.rows:
                colour = "#3fe0d0" if r["sent"] else "#ff7a45"
                who = html.escape(r["who"])
                tag = " checked in" if r["checkin"] else ""
                where = destination_text(r.get("to_id"), snap.get("self_id"), snap.get("node_info"), r["sent"])
                if where:
                    tag += f' <span style="color:#9a90a6">{html.escape(where)}</span>'
                if r["state"]:
                    tag += f' <span style="color:#9a90a6">({html.escape(r["state"])})</span>'
                html_rows.append(f'<p style="margin:2px 0"><span style="color:#9a90a6">{r["time"]}  ch{r["channel"]}</span> '
                                 f'<b style="color:{colour}">{who}</b>{tag}<br>{html.escape(r["text"])}</p>')
            bar = self.view.verticalScrollBar()
            where, keep = scroll_after_fill(self.first_fill, bar.value(), bar.maximum()), bar.value()
            self.view.setHtml("".join(html_rows) or "<p style='color:#9a90a6'>Nothing heard on the mesh yet.</p>")
            bar.setValue(bar.maximum() if where == "bottom" else keep)
            if where == "bottom":
                # the page may not be laid out yet, so its true end is only known once Qt has done that
                QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
            if first_fill_done(bool(self.rows), self.view.isVisible()):
                self.first_fill = False
            incoming = sum(1 for r in self.rows if not r["sent"])
            self.count.setText(f"{len(self.rows)} messages, {incoming} heard" if self.rows else "No messages yet.")

    class RadarPage(QWidget):
        def __init__(self):
            super().__init__()
            self.engine = None
            root = QHBoxLayout(self)
            left = QFrame()
            left.setObjectName("card")
            ll = QVBoxLayout(left)
            bar = QHBoxLayout()
            self.ring = QLabel("")
            self.ring.setStyleSheet("font-size: 13pt;")
            less = QPushButton("Ring -")
            more = QPushButton("Ring +")
            for b, step in ((less, -1), (more, 1)):
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.clicked.connect(lambda _=False, s=step: self.adjust(s))
            bar.addWidget(self.ring, 1)
            bar.addWidget(less)
            bar.addWidget(more)
            ll.addLayout(bar)
            self.radar = Radar()
            ll.addWidget(self.radar, 1)
            root.addWidget(left, 3)
            right = QFrame()
            right.setObjectName("card")
            rl = QVBoxLayout(right)
            cap = QLabel("Alerts")
            cap.setStyleSheet("font-size: 13pt; color: #9e9e9e;")
            self.alerts = QPlainTextEdit()
            self.alerts.setReadOnly(True)
            self.alerts.setStyleSheet("font-size: 11pt;")
            self.legend = QLabel("Green: inside the ring.  Amber: outside.  Grey: the position is more than "
                                 "15 minutes old, or of unknown age, even if the node was heard since. Each dot "
                                 "says how old its position is; rest the pointer on the radar for the list.")
            self.legend.setWordWrap(True)
            self.legend.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
            rl.addWidget(cap)
            rl.addWidget(self.alerts, 1)
            rl.addWidget(self.legend)
            root.addWidget(right, 2)

        def adjust(self, step):
            if self.engine:
                self.engine.adjust_proximity_radius(step)

        def update_from(self, engine, snap, own):
            self.engine = engine
            radius = snap.get("proximity_radius_km") or 1.0
            self.ring.setText(f"Proximity ring {radius:g} km")
            points, span = radar_points(snap["node_info"], snap.get("self_id"), own, radius)
            self.radar.points, self.radar.span, self.radar.radius = points, span, radius
            self.radar.waiting = own is None
            self.radar.setToolTip(radar_tooltip(points))
            self.radar.update()
            lines = alert_lines(snap.get("recent_alerts"), engine.stale_nodes(), snap["node_info"])
            self.alerts.setPlainText("\n".join(lines) if lines else "No alerts.")

    class Panel(FolderTabWidget):
        def __init__(self):
            super().__init__()
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setObjectName("subtabs")
            self.mesh = MeshPage()
            self.radar_page = RadarPage()
            self.messages = MessagesPage()
            self.messages.on_reply = self.go_reply
            self.mesh.on_snap = self.fan_out
            self.addTab(self.mesh, "Mesh")
            self.addTab(self.messages, "Messages")
            self.addTab(self.radar_page, "Radar")
            self.currentChanged.connect(self.tab_changed)

        def fan_out(self, engine, snap, own):
            self.radar_page.update_from(engine, snap, own)
            self.messages.update_from(snap)
            rows = message_rows(chat_events(snap))
            if self.currentWidget() is self.messages:
                self.messages.read_mark = row_signature(rows[-1]) if rows else self.messages.read_mark
            unread = unread_count(rows, self.messages.read_mark)
            self.setTabText(1, f"Messages ({unread})" if unread else "Messages")

        def tab_changed(self, _=0):
            if self.currentWidget() is self.messages:
                self.messages.shown()
            if self.currentWidget() is self.messages and self.messages.rows:
                self.messages.read_mark = row_signature(self.messages.rows[-1])
                self.setTabText(1, "Messages")

        def go_reply(self):
            self.setCurrentWidget(self.mesh)
            self.mesh.entry.setFocus()

        def activate(self):
            self.mesh.activate()

        def deactivate(self):
            self.mesh.deactivate()

    panel = Panel()
    return panel, panel.activate, panel.deactivate
