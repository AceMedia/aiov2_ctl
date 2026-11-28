import collections
import copy
import os
import subprocess
import threading
import time
from datetime import datetime

from . import bootstrap, companions
from .power import debug, log
from . import delivery, geo, nodeid


def _pubsub():
    """The publish and subscribe bus the Meshtastic library announces packets and connection changes on, or
    None when it is not installed. A function of its own so a test can put a stand-in in its place."""
    try:
        from pubsub import pub
        return pub
    except Exception:
        return None


SERIAL_CANDIDATES = ("/dev/ttyACM0", "/dev/ttyUSB0", "/dev/ttyACM1", "/dev/ttyUSB1")

# USB chips that Meshtastic boards actually use, as (vendor id, product id or None for any, name). A serial port
# whose USB identity is not one of these is never opened: it may be a GPS, a Flipper, a modem or anything else.
MESHTASTIC_USB_CHIPS = (
    ("303a", None, "Espressif USB (ESP32-S3/C3)"),
    ("10c4", "ea60", "Silicon Labs CP210x"),
    ("1a86", None, "WCH CH340/CH9102"),
    ("239a", None, "Adafruit nRF52"),
    ("2e8a", None, "Raspberry Pi RP2040"),
)


def usb_chip(identity):
    """The name of the Meshtastic chip a (vendor, product) identity is, or None when it is not one of them."""
    if not identity:
        return None
    vid, pid = (str(x or "").lower() for x in identity)
    for chip_vid, chip_pid, name in MESHTASTIC_USB_CHIPS:
        if vid == chip_vid and (chip_pid is None or pid == chip_pid):
            return name
    return None


def _sysfs_usb_identity(device):
    """(vendor, product) for a tty from /sys/class/tty/<name>/device: the USB device is that directory's parent
    (ttyACM) or grandparent (ttyUSB), the first one up the tree with an idVendor file."""
    here = os.path.realpath(os.path.join("/sys/class/tty", os.path.basename(device), "device"))
    for _ in range(4):
        try:
            with open(os.path.join(here, "idVendor")) as v, open(os.path.join(here, "idProduct")) as p:
                return v.read().strip().lower(), p.read().strip().lower()
        except OSError:
            here = os.path.dirname(here)
    return None


def usb_identity(device):
    """(vendor id, product id) as four hex digits each for a serial device, or None when it cannot be read. pyserial's
    port list when the library is there, otherwise sysfs. A function of its own so a test can put a stand-in in its
    place."""
    try:
        from serial.tools import list_ports
        for port in list_ports.comports():
            if port.device == device and port.vid is not None:
                return f"{port.vid:04x}", f"{(port.pid or 0):04x}"
    except Exception:
        pass
    return _sysfs_usb_identity(device)


def choose_serial_port(devices, identify):
    """(the port to open or None, the reason in words). Only a port whose USB identity is a chip Meshtastic boards
    use qualifies; the first such port is chosen. Nothing is left to the library's auto-detection."""
    notes = []
    for device in devices:
        short = device.split("/")[-1]
        try:
            identity = identify(device)
        except Exception:
            identity = None
        if not identity:
            notes.append(f"{short}: USB identity unknown")
            continue
        label = f"{identity[0]}:{identity[1]}"
        chip = usb_chip(identity)
        if chip:
            return device, f"{short} is a {chip} ({label})"
        notes.append(f"{short} ({label}) is not a chip Meshtastic boards use")
    if not notes:
        return None, "No USB serial devices found"
    return None, "No Meshtastic radio on USB serial: " + "; ".join(notes)


CHANNEL_DISABLED, CHANNEL_PRIMARY = 0, 1          # the library's Channel.Role values (SECONDARY is 2)


def configured_channels(channels):
    """The channels set up on the radio, from the library's channel objects (interface.localNode.channels): a list
    of {"index", "name", "primary"}, the primary first and then by index, disabled ones left out. The primary's name
    may be empty, which means the radio's default. None when there is nothing usable, so the caller can fall back
    to the channels heard lately. Only reads the objects: the radio's configuration is never changed."""
    try:
        items = list(channels or [])
    except TypeError:
        return None
    found = []
    for ch in items:
        try:
            role = int(getattr(ch, "role", CHANNEL_DISABLED) or 0)
            index = int(getattr(ch, "index", 0) or 0)
        except (TypeError, ValueError):
            continue
        if role == CHANNEL_DISABLED:
            continue
        name = getattr(getattr(ch, "settings", None), "name", "") or ""
        found.append({"index": index, "name": str(name).strip(), "primary": role == CHANNEL_PRIMARY})
    found.sort(key=lambda c: (not c["primary"], c["index"]))
    return found or None


# The library's LoRa modem presets (Config.LoRaConfig.ModemPreset) by number, written as the radio names a primary
# channel whose own name is empty. An assumption from the library's protobuf, not checked against a radio.
MODEM_PRESETS = ("LongFast", "LongSlow", "VeryLongSlow", "MediumSlow", "MediumFast", "ShortSlow", "ShortFast",
                 "LongModerate", "ShortTurbo")


def modem_preset_name(lora_config):
    """The preset the radio is set to ("LongFast"), from the library's LoRa config (interface.localNode.localConfig
    .lora), or None when it is not using a preset or the config is not available. Only reads it."""
    if lora_config is None or not getattr(lora_config, "use_preset", False):
        return None
    try:
        number = int(getattr(lora_config, "modem_preset", 0) or 0)
    except (TypeError, ValueError):
        return None
    return MODEM_PRESETS[number] if 0 <= number < len(MODEM_PRESETS) else None


def explain_tcp_failure(exc, host, port):
    """A short, useful status line for a failed Meshtastic TCP connection."""
    local = host in ("localhost", "127.0.0.1", "::1")
    if isinstance(exc, ConnectionRefusedError) or "refused" in str(exc).lower():
        if local:
            return ("meshtasticd is not running. Turn the LORA rail on, then: "
                    "sudo systemctl start meshtasticd")
        return f"Nothing is listening on {host}:{port}"
    if isinstance(exc, (TimeoutError, OSError)) and "timed out" in str(exc).lower():
        return f"Timed out reaching {host}:{port}"
    return str(exc)[:50]


class LoRa:
    ACTIVE_CHANNEL_WINDOW_SECONDS = 1800

    def __init__(self):
        self.lock = threading.RLock()
        self.persist_callback = None
        self.active = False
        self.interface = None
        self.messages = []
        self.max_messages = 20
        self.activities = []
        self.max_activities = 40
        # Chat (text messages and check-ins) is also kept on its own, so a burst of telemetry or position
        # packets cannot push it out of `activities`. Memory only: nothing here is written to disk.
        self.chat = []
        self.max_chat = 300
        self.node_info = {}
        self.node_history = {}
        self.max_track_points = 8
        self.recent_alerts = []
        self.max_alerts = 6
        self.recent_checkins = []
        self.max_checkins = 6
        self.channel_activity = {}
        self.configured_channels = None     # the radio's own channel list, when the connection gives it
        self.modem_preset = None            # the radio's preset name ("LongFast"), when the connection gives it
        self.selected_channel = 0
        self.auto_follow_channel = False
        self.channel_locked = False
        self.proximity_radius_km = 1.0
        self.proximity_state = {}
        self.connecting = False
        self.connection_status = "Not started"
        self.last_rx_time = None
        self.last_rx_summary = None
        self.last_tx_time = None
        self.last_tx_status = None
        self.last_tx_ok = None
        self.conflict_processes = []
        self._bus = None                    # the bus this is subscribed on, while it is
        self._seen_packets = collections.deque(maxlen=200)
        self.deliveries = {}                # packet id -> the delivery record of a sent message still awaiting an answer

        if not bootstrap.MESHTASTIC_AVAILABLE:
            debug("LoRa: meshtastic library not available")
            log("LoRa: Install with 'pip3 install meshtastic'")
            self.connection_status = "Library not installed"

    def _persist_state(self):
        callback = self.persist_callback
        if callable(callback):
            try:
                callback()
            except Exception as e:
                debug(f"LoRa: persist callback failed: {e}")

    def snapshot(self):
        with self.lock:
            self._update_deliveries_locked(time.time())
            self_id = self._own_id_locked()
            selected_channel, best_channel, active_channels = self._effective_selected_channel_locked()
            return {
                "active": self.active,
                "connecting": self.connecting,
                "connection_status": self.connection_status,
                "messages": copy.deepcopy(self.messages),
                "activities": copy.deepcopy(self.activities),
                "chat": copy.deepcopy(self.chat),
                "node_info": copy.deepcopy(self.node_info),
                "node_history": copy.deepcopy(self.node_history),
                "recent_alerts": copy.deepcopy(self.recent_alerts),
                "recent_checkins": copy.deepcopy(self.recent_checkins),
                "channel_activity": copy.deepcopy(self.channel_activity),
                "configured_channels": copy.deepcopy(self.configured_channels),
                "modem_preset": self.modem_preset,
                "active_channels": active_channels,
                "selected_channel": selected_channel,
                "best_channel": best_channel,
                "auto_follow_channel": self.auto_follow_channel,
                "channel_locked": self.channel_locked,
                "proximity_radius_km": self.proximity_radius_km,
                "last_rx_time": self.last_rx_time,
                "last_rx_summary": self.last_rx_summary,
                "last_tx_time": self.last_tx_time,
                "last_tx_status": self.last_tx_status,
                "last_tx_ok": self.last_tx_ok,
                "conflict_processes": copy.deepcopy(self.conflict_processes),
                "self_id": self_id,
            }

    def _own_id_locked(self):
        """This radio's own node, written the way the node list writes its nodes ("!xxxxxxxx"), or None. The
        library gives it as a number; left as a number it never matched a name in the list, so the app did
        not know which node was its own."""
        iface = self.interface
        if not iface:
            return None
        number = getattr(getattr(iface, "myInfo", None), "my_node_num", None)
        for attr in ("localNode", "myNode"):
            if number is None:
                number = getattr(getattr(iface, attr, None), "nodeNum", None)
        return nodeid.canonical(number)

    # ---- hearing from the radio
    # The library publishes every packet, and the loss of the connection, on a bus (pubsub). It used to offer a
    # hook on the connection object as well, and this code set that; the library on a uConsole today has no such
    # hook, so nothing was ever received: connected, a node list, and no messages.
    def _listen(self):
        """Start receiving packets and connection news for this connection. True when subscribed on the bus."""
        bus = _pubsub()
        if bus is None:
            if hasattr(self.interface, "messageReceived"):          # a library old enough to have the hook
                self.interface.messageReceived = self._on_receive
                debug("LoRa: message hook set (no pubsub)")
            else:
                debug("LoRa: no way to receive packets from this library")
            return False
        bus.subscribe(self._on_receive, "meshtastic.receive")
        bus.subscribe(self._on_routing, "meshtastic.receive.routing")
        bus.subscribe(self._on_lost, "meshtastic.connection.lost")
        self._bus = bus
        debug("LoRa: subscribed for packets and connection changes")
        return True

    def _stop_listening(self):
        bus, self._bus = self._bus, None
        if bus is not None:
            for listener, topic in ((self._on_receive, "meshtastic.receive"),
                                    (self._on_routing, "meshtastic.receive.routing"),
                                    (self._on_lost, "meshtastic.connection.lost")):
                try:
                    bus.unsubscribe(listener, topic)
                except Exception as e:
                    debug(f"LoRa: unsubscribe failed: {e}")

    def _on_receive(self, packet, interface=None):
        """A packet from the bus. Only this connection's packets count, and each counts once: the bus is shared
        by every connection in the program, and a reconnect must not deliver a packet a second time."""
        try:
            if interface is not None and self.interface is not None and interface is not self.interface:
                return
            if not isinstance(packet, dict):
                return
            mark = (packet.get("from"), packet.get("id"))
            if mark[1] is not None:
                with self.lock:
                    if mark in self._seen_packets:
                        return
                    self._seen_packets.append(mark)
            self._handle_packet(packet)
        except Exception as e:
            debug(f"LoRa: packet handler error: {e}")

    def _on_routing(self, packet, interface=None):
        """A routing packet: the library's answer about a packet this app sent with an acknowledgement asked for.
        Only answers about this connection's own waiting messages change anything (see ops_agent/delivery.py)."""
        try:
            if interface is not None and self.interface is not None and interface is not self.interface:
                return
            reply = delivery.routing_reply(packet)
            if reply is None:
                return
            request, reason = reply
            with self.lock:
                record = self.deliveries.get(request)
                if record is None:
                    return
                record.update(delivery.after_routing(record, reason))
                if delivery.can_forget(record, time.time()):
                    self.deliveries.pop(request, None)
            debug(f"LoRa: answer for packet {request}: {reason}")
        except Exception as e:
            debug(f"LoRa: routing handler error: {e}")

    def _update_deliveries_locked(self, now):
        """Mark messages that have waited too long as not acknowledged, and stop following old ones."""
        for key, record in list(self.deliveries.items()):
            record.update(delivery.after_time(record, now))
            if delivery.can_forget(record, now):
                del self.deliveries[key]

    def _on_lost(self, interface=None):
        """The library says the connection has gone. Say so, so nothing goes on showing it as connected."""
        if interface is not None and self.interface is not None and interface is not self.interface:
            return
        with self.lock:
            if not self.active:
                return
            self.active = False
            self.connection_status = "Connection lost"
        log("LoRa: mesh connection lost")

    def close(self):
        """Stop listening and close the connection. Safe to call twice, and when it never connected."""
        self._stop_listening()
        with self.lock:
            iface, self.interface = self.interface, None
            self.active = False
            if self.connection_status.startswith("Connected"):
                self.connection_status = "Closed"
        if iface is not None:
            try:
                iface.close()
            except Exception as e:
                debug(f"LoRa: close failed: {e}")

    def _append_bounded(self, bucket, item, max_items):
        bucket.append(item)
        if len(bucket) > max_items:
            bucket.pop(0)

    def _append_activity(self, item):
        self._append_bounded(self.activities, item, self.max_activities)
        if isinstance(item, dict) and item.get("kind") in ("text", "checkin"):
            self._append_bounded(self.chat, item, self.max_chat)

    def _normalize_channel(self, channel):
        try:
            return int(channel)
        except (TypeError, ValueError):
            return 0

    def _active_channels_locked(self):
        now = time.time()
        channels = []
        for channel, stats in sorted(self.channel_activity.items()):
            last_seen = stats.get("last_seen")
            if last_seen is None or (now - last_seen) > self.ACTIVE_CHANNEL_WINDOW_SECONDS:
                continue
            item = {
                "channel": int(channel),
                "count": int(stats.get("count", 0)),
                "rx_count": int(stats.get("rx_count", 0)),
                "tx_count": int(stats.get("tx_count", 0)),
                "last_seen": last_seen,
            }
            channels.append(item)
        channels.sort(key=lambda item: (-(item["last_seen"] or 0), -item["count"], item["channel"]))
        return channels

    def _scouted_channels_locked(self):
        channels = self._active_channels_locked()
        if not channels:
            return channels

        scout = {item["channel"]: dict(item) for item in channels}
        for item in scout.values():
            item["unique_nodes"] = 0
            item["text_count"] = 0
            item["telemetry_count"] = 0
            item["position_count"] = 0
            item["checkin_count"] = 0
            item["avg_rssi"] = None
            item["avg_snr"] = None
            item["score"] = 0.0

        node_sets = {channel: set() for channel in scout}
        rssi_samples = {channel: [] for channel in scout}
        snr_samples = {channel: [] for channel in scout}

        for activity in self.activities:
            channel = self._normalize_channel(activity.get("channel", 0))
            if channel not in scout:
                continue
            from_id = str(activity.get("from_id", ""))
            if from_id and from_id != "Me":
                node_sets[channel].add(from_id)
            kind = activity.get("kind")
            if kind == "text":
                scout[channel]["text_count"] += 1
            elif kind == "checkin":
                scout[channel]["checkin_count"] += 1
            elif kind == "telemetry":
                scout[channel]["telemetry_count"] += 1
            elif kind == "position":
                scout[channel]["position_count"] += 1

            if not activity.get("sent"):
                rx_rssi = activity.get("rx_rssi")
                rx_snr = activity.get("rx_snr")
                if rx_rssi is not None:
                    try:
                        rssi_samples[channel].append(float(rx_rssi))
                    except (TypeError, ValueError):
                        pass
                if rx_snr is not None:
                    try:
                        snr_samples[channel].append(float(rx_snr))
                    except (TypeError, ValueError):
                        pass

        for checkin in self.recent_checkins:
            channel = self._normalize_channel(checkin.get("channel", 0))
            if channel in scout:
                scout[channel]["checkin_count"] += 1

        now = time.time()
        for channel, item in scout.items():
            item["unique_nodes"] = len(node_sets[channel])
            if rssi_samples[channel]:
                item["avg_rssi"] = sum(rssi_samples[channel]) / len(rssi_samples[channel])
            if snr_samples[channel]:
                item["avg_snr"] = sum(snr_samples[channel]) / len(snr_samples[channel])

            age = max(0.0, now - (item["last_seen"] or now))
            freshness = max(0.0, 20.0 - min(20.0, age / 15.0))
            traffic = min(item["rx_count"], 10) * 3.0 + min(item["tx_count"], 5) * 1.0
            nodes = min(item["unique_nodes"], 6) * 6.0
            text = min(item["text_count"], 6) * 3.0
            telemetry = min(item["telemetry_count"] + item["position_count"], 6) * 2.0
            checkins = min(item["checkin_count"], 4) * 5.0
            quality = 0.0
            if item["avg_snr"] is not None:
                quality += max(0.0, min(10.0, item["avg_snr"] + 5.0))
            if item["avg_rssi"] is not None:
                quality += max(0.0, min(10.0, (item["avg_rssi"] + 120.0) / 2.0))
            item["score"] = round(freshness + traffic + nodes + text + telemetry + checkins + quality, 1)

        ranked = sorted(
            scout.values(),
            key=lambda item: (-item["score"], -(item["last_seen"] or 0), -item["count"], item["channel"]),
        )
        return ranked

    def _effective_selected_channel_locked(self, active_channels=None):
        if active_channels is None:
            active_channels = self._scouted_channels_locked()
        best_channel = active_channels[0]["channel"] if active_channels else None
        active_channel_ids = [item["channel"] for item in active_channels]
        selected_channel = self.selected_channel
        # The channel the person chose stays chosen. It used to be swapped for the busiest one whenever it had
        # been quiet for a while, locked or not, so a message could go out on a channel nobody picked. Only
        # Follow, when the channel is not locked, moves it.
        if self.auto_follow_channel and not self.channel_locked and best_channel is not None:
            selected_channel = best_channel
        self.selected_channel = selected_channel
        return selected_channel, best_channel, active_channels

    def _remember_channel_activity(self, channel, sent=False):
        channel = self._normalize_channel(channel)
        now = time.time()
        with self.lock:
            stats = self.channel_activity.setdefault(
                channel,
                {"count": 0, "rx_count": 0, "tx_count": 0, "last_seen": None},
            )
            stats["count"] += 1
            if sent:
                stats["tx_count"] += 1
            else:
                stats["rx_count"] += 1
            stats["last_seen"] = now
            self._effective_selected_channel_locked()
        self._persist_state()

    def cycle_active_channel(self, step):
        with self.lock:
            active_channels = self._scouted_channels_locked()
            channels = [item["channel"] for item in active_channels]
            if not channels:
                return None
            current_channel, _, _ = self._effective_selected_channel_locked(active_channels)
            if self.auto_follow_channel and not self.channel_locked:
                self.channel_locked = True
            current = channels.index(current_channel)
            self.selected_channel = channels[(current + step) % len(channels)]
            selected = self.selected_channel
        self._persist_state()
        return selected

    def set_selected_channel(self, channel):
        selected = self._normalize_channel(channel)
        with self.lock:
            self.selected_channel = selected
        self._persist_state()
        return selected

    def jump_to_best_channel(self):
        with self.lock:
            _, best_channel, active_channels = self._effective_selected_channel_locked()
            if not active_channels or best_channel is None:
                return None
            self.selected_channel = best_channel
        self._persist_state()
        return best_channel

    def toggle_auto_follow_channel(self):
        with self.lock:
            self.auto_follow_channel = not self.auto_follow_channel
            enabled = self.auto_follow_channel
            selected_channel, _, _ = self._effective_selected_channel_locked()
        self._persist_state()
        return enabled, selected_channel

    def toggle_channel_lock(self):
        with self.lock:
            self.channel_locked = not self.channel_locked
            locked = self.channel_locked
            selected_channel, _, _ = self._effective_selected_channel_locked()
        self._persist_state()
        return locked, selected_channel

    def _append_alert(self, message):
        with self.lock:
            entry = {"time": datetime.now().strftime("%H:%M:%S"), "message": message}
            if self.recent_alerts and self.recent_alerts[-1]["message"] == message:
                return
            self._append_bounded(self.recent_alerts, entry, self.max_alerts)
        self._persist_state()

    def _append_checkin(self, entry):
        if not isinstance(entry, dict):
            return
        with self.lock:
            self._append_bounded(self.recent_checkins, entry, self.max_checkins)
        self._persist_state()

    def adjust_proximity_radius(self, step):
        with self.lock:
            radii = [0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
            try:
                idx = min(range(len(radii)), key=lambda i: abs(radii[i] - self.proximity_radius_km))
            except ValueError:
                idx = 2
            idx = max(0, min(len(radii) - 1, idx + step))
            new_radius = radii[idx]
            if abs(new_radius - self.proximity_radius_km) < 1e-9:
                return self.proximity_radius_km
            self.proximity_radius_km = new_radius
            self.proximity_state = {}
            message = f"Proximity ring set to {self.proximity_radius_km:.2f}km"
        self._append_alert(message)
        log(f"LoRa: {message}")
        return new_radius

    def _set_tx_status(self, ok, status):
        with self.lock:
            self.last_tx_ok = ok
            self.last_tx_status = status
            self.last_tx_time = time.time()

    def _detect_serial_conflicts(self, device_paths):
        conflicts = []
        try:
            result = subprocess.run(
                ["lsof", "-t"] + list(device_paths),
                capture_output=True,
                text=True,
                timeout=1,
            )
        except Exception:
            return conflicts

        pids = [pid.strip() for pid in result.stdout.splitlines() if pid.strip()]
        for pid in pids:
            try:
                proc_info = subprocess.run(
                    ["ps", "-p", pid, "-o", "comm="],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
            except Exception:
                continue
            proc_name = proc_info.stdout.strip() or "unknown"
            conflicts.append({"pid": pid, "name": proc_name})
        return conflicts

    def _node_entry(self, node_id):
        with self.lock:
            for key in (node_id, str(node_id)):
                info = self.node_info.get(key)
                if isinstance(info, dict):
                    return info
            return None

    def _remember_node_info(self, node_id, **sections):
        if node_id is None:
            return

        key = str(node_id)
        with self.lock:
            entry = self.node_info.get(key)
            if not isinstance(entry, dict):
                entry = {}
                self.node_info[key] = entry

            for name, value in sections.items():
                if not isinstance(value, dict) or not value:
                    continue
                current = entry.get(name)
                if isinstance(current, dict):
                    current.update(value)
                else:
                    entry[name] = dict(value)
                if name == "position":
                    self._remember_node_position(key, value)

    def _position_timestamp(self, position):
        if not isinstance(position, dict):
            return time.time()
        raw = position.get("time")
        if raw is None:
            return time.time()
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return time.time()
        if value > 1_000_000_000_000:
            value /= 1000.0
        return value

    def _remember_node_position(self, node_id, position):
        if not isinstance(position, dict):
            return
        where = geo.latlon(position)
        if where is None:
            return
        lat, lon = where

        point = {
            "time": self._position_timestamp(position),
            "lat": lat,
            "lon": lon,
            "alt": position.get("altitude"),
        }
        key = str(node_id)
        with self.lock:
            history = self.node_history.setdefault(key, [])
            if history:
                last = history[-1]
                if abs(last["lat"] - point["lat"]) < 1e-6 and abs(last["lon"] - point["lon"]) < 1e-6:
                    if abs(last["time"] - point["time"]) < 1:
                        return
            history.append(point)
            if len(history) > self.max_track_points:
                del history[:-self.max_track_points]

    def _remember_position_received(self, node_id, position, packet):
        """Note when this app received a node's position, apart from when the node was last heard: a node heard a
        minute ago may still be showing a position it sent hours ago. When the new position carries no time of its
        own, a time kept from an older position must not go on standing for it, so it is removed."""
        if node_id is None:
            return
        received = packet.get("rxTime") if isinstance(packet, dict) else None
        try:
            received = float(received) if received else time.time()
        except (TypeError, ValueError):
            received = time.time()
        with self.lock:
            entry = self.node_info.get(str(node_id))
            if not isinstance(entry, dict):
                return
            entry["positionReceived"] = received
            stored = entry.get("position")
            if isinstance(stored, dict) and not position.get("time"):
                stored.pop("time", None)

    def _remember_telemetry_received(self, node_id, packet):
        """Note when this app received a node's own report of its battery and channel use: the library keeps the
        figures but not when they came, and the node details say how old they are."""
        if node_id is None:
            return
        received = packet.get("rxTime") if isinstance(packet, dict) else None
        try:
            received = float(received) if received else time.time()
        except (TypeError, ValueError):
            received = time.time()
        with self.lock:
            entry = self.node_info.get(str(node_id))
            if isinstance(entry, dict):
                entry["telemetryReceived"] = received

    def _remember_link_metrics(self, node_id, packet):
        if node_id is None or not isinstance(packet, dict):
            return
        metrics = {}
        if packet.get("rxRssi") is not None:
            metrics["rxRssi"] = packet.get("rxRssi")
        if packet.get("rxSnr") is not None:
            metrics["rxSnr"] = packet.get("rxSnr")
        if packet.get("hopLimit") is not None:
            metrics["hopLimit"] = packet.get("hopLimit")
        if packet.get("hopStart") is not None:
            metrics["hopStart"] = packet.get("hopStart")
        if metrics:
            metrics["time"] = time.time()
            self._remember_node_info(node_id, linkMetrics=metrics)

    def _node_name(self, node_id):
        info = self._node_entry(node_id)
        if isinstance(info, dict):
            user = info.get("user", {})
            return user.get("shortName") or user.get("longName") or str(node_id)
        return str(node_id)

    def _portnum_name(self, packet):
        decoded = packet.get("decoded", {}) if isinstance(packet, dict) else {}
        portnum = decoded.get("portnum") or packet.get("portnum") or "UNKNOWN_APP"
        portnum = str(portnum)
        if "." in portnum:
            portnum = portnum.rsplit(".", 1)[-1]
        return portnum

    def _extract_metrics(self, packet):
        decoded = packet.get("decoded", {}) if isinstance(packet, dict) else {}
        candidates = []
        if isinstance(decoded, dict):
            candidates.append(decoded)
            candidates.extend(value for value in decoded.values() if isinstance(value, dict))
            # the library gives a telemetry packet as decoded["telemetry"]["deviceMetrics"], one level further down
            for value in list(candidates[1:]):
                candidates.extend(inner for inner in value.values() if isinstance(inner, dict))

        metrics = {}
        for candidate in candidates:
            for key in (
                "batteryLevel",
                "voltage",
                "channelUtilization",
                "airUtilTx",
                "uptimeSeconds",
            ):
                value = candidate.get(key)
                if value is not None and key not in metrics:
                    metrics[key] = value
        return metrics

    def _parse_checkin_text(self, text):
        if not isinstance(text, str):
            return None
        text = text.strip()
        if not text.startswith("CHECKIN "):
            return None
        result = {"raw": text, "note": ""}
        for token in text.split()[1:]:
            if "=" not in token:
                if result["note"]:
                    result["note"] += " "
                result["note"] += token
                continue
            key, value = token.split("=", 1)
            if key == "lat":
                try:
                    result["lat"] = float(value)
                except ValueError:
                    pass
            elif key == "lon":
                try:
                    result["lon"] = float(value)
                except ValueError:
                    pass
            elif key == "alt":
                try:
                    result["alt"] = float(value)
                except ValueError:
                    result["alt"] = value
            elif key == "note":
                result["note"] = value.replace("_", " ")
        return result if "lat" in result and "lon" in result else None

    def _remember_checkin(self, node_name, node_id, parsed, sent=False, channel=0):
        if not parsed:
            return
        self._append_checkin(
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "from": node_name,
                "from_id": str(node_id),
                "channel": self._normalize_channel(channel),
                "lat": parsed.get("lat"),
                "lon": parsed.get("lon"),
                "alt": parsed.get("alt"),
                "note": parsed.get("note") or "",
                "sent": sent,
            }
        )

    def format_checkin_message(self, location, note=""):
        if not isinstance(location, dict):
            return None
        lat = location.get("lat")
        lon = location.get("lon")
        if lat is None or lon is None:
            return None
        parts = [f"CHECKIN lat={float(lat):.5f}", f"lon={float(lon):.5f}"]
        alt = location.get("alt")
        if alt is not None:
            try:
                parts.append(f"alt={float(alt):.0f}")
            except (TypeError, ValueError):
                pass
        if note:
            safe_note = "_".join(str(note).strip().split())
            if safe_note:
                parts.append(f"note={safe_note}")
        return " ".join(parts)

    def update_proximity(self, local_position, distance_fn):
        if not isinstance(local_position, dict):
            return
        lat = local_position.get("lat")
        lon = local_position.get("lon")
        own = geo.latlon({"latitude": lat, "longitude": lon})
        if own is None:
            return
        with self.lock:
            node_items = list(self.node_info.items())
        for node_id, info in node_items:
            pos = info.get("position") if isinstance(info, dict) else None
            if not isinstance(pos, dict):
                continue
            where = geo.latlon(pos)
            if where is None:
                continue
            try:
                distance_km = distance_fn(own[0], own[1], where[0], where[1])
            except (TypeError, ValueError):
                continue
            inside = distance_km <= self.proximity_radius_km
            with self.lock:
                previous = self.proximity_state.get(str(node_id))
            if previous is None:
                with self.lock:
                    self.proximity_state[str(node_id)] = inside
                continue
            if previous != inside:
                name = self._node_name(node_id)
                state = "entered" if inside else "left"
                message = f"{name} {state} {self.proximity_radius_km:.1f}km ring ({distance_km:.1f}km)"
                self._append_alert(message)
                log(f"LoRa: {message}")
                with self.lock:
                    self.proximity_state[str(node_id)] = inside

    def stale_nodes(self, stale_after_seconds=900):
        stale = []
        now = time.time()
        with self.lock:
            node_items = list(self.node_info.items())
        for node_id, info in node_items:
            if not isinstance(info, dict):
                continue
            last_heard = info.get("lastHeard") or info.get("lastHeardTime")
            if last_heard is None:
                position = info.get("position")
                if isinstance(position, dict):
                    last_heard = position.get("time")
            if last_heard is None:
                continue
            try:
                value = float(last_heard)
            except (TypeError, ValueError):
                continue
            if value > 1_000_000_000_000:
                value /= 1000.0
            age = max(0.0, now - value)
            if age >= stale_after_seconds:
                stale.append((str(node_id), age))
        stale.sort(key=lambda item: item[1], reverse=True)
        return stale

    def _build_activity(self, packet, sent=False, text_override=None):
        decoded = packet.get("decoded", {}) if isinstance(packet, dict) else {}
        timestamp = datetime.now().strftime("%H:%M:%S")
        from_id = packet.get("fromId", "Me" if sent else "Unknown")
        node_name = "Me" if sent else self._node_name(from_id)
        portnum = self._portnum_name(packet)
        channel = self._normalize_channel(packet.get("channel", 0))

        summary = "Packet received"
        kind = "event"

        text = text_override if text_override is not None else decoded.get("text")
        if isinstance(text, str) and text.strip():
            kind = "text"
            summary = text.strip().replace("\n", " ")
            parsed_checkin = self._parse_checkin_text(text)
            if parsed_checkin:
                kind = "checkin"
                note = parsed_checkin.get("note")
                suffix = f" {note}" if note else ""
                summary = f"Check-in {parsed_checkin['lat']:.5f},{parsed_checkin['lon']:.5f}{suffix}"
                self._remember_checkin(node_name, from_id, parsed_checkin, sent=sent, channel=channel)
        elif not decoded and packet.get("encrypted") is not None:
            # the library hands on a packet it could not decrypt with "encrypted" and no "decoded": a direct message
            # encrypted to another node, or a channel this radio has no key for. Its content is not shown.
            kind = "encrypted"
            summary = "Encrypted, cannot be read here"
        else:
            position = decoded.get("position")
            if isinstance(position, dict) and position:
                self._remember_node_info(from_id, position=position)
                self._remember_position_received(from_id, position, packet)
                lat = position.get("latitude")
                lon = position.get("longitude")
                alt = position.get("altitude")
                pos_bits = []
                if lat is not None and lon is not None:
                    pos_bits.append(f"{lat:.5f},{lon:.5f}")
                if alt is not None:
                    pos_bits.append(f"{alt}m")
                summary = "Pos " + (" ".join(pos_bits) if pos_bits else "update")
                kind = "position"
            else:
                user = decoded.get("user")
                if isinstance(user, dict) and user:
                    self._remember_node_info(from_id, user=user)
                    short = user.get("shortName") or user.get("longName") or "update"
                    summary = f"Identity {short}"
                    kind = "user"
                else:
                    metrics = self._extract_metrics(packet)
                    if metrics:
                        self._remember_node_info(from_id, deviceMetrics=metrics)
                        self._remember_telemetry_received(from_id, packet)
                        parts = []
                        battery = metrics.get("batteryLevel")
                        voltage = metrics.get("voltage")
                        channel_use = metrics.get("channelUtilization")
                        air_use = metrics.get("airUtilTx")
                        if battery is not None:
                            parts.append(f"{battery}%")
                        if voltage is not None:
                            parts.append(f"{voltage}V")
                        if channel_use is not None:
                            parts.append(f"Ch {channel_use}%")
                        if air_use is not None:
                            parts.append(f"Air {air_use}%")
                        summary = "Telemetry " + (" ".join(parts) if parts else "update")
                        kind = "telemetry"
                    else:
                        summary = portnum.replace("_APP", "").replace("_", " ").title()

        activity = {
            "time": timestamp,
            "from": node_name,
            "from_id": str(from_id),
            # where the packet went: "^all" (everyone on the channel) or a node. This app only ever sends to everyone.
            "to_id": "^all" if sent else (packet.get("toId") if packet.get("toId") is not None else packet.get("to")),
            "channel": channel,
            "portnum": portnum,
            "kind": kind,
            "summary": summary,
            "sent": sent,
            "rx_snr": packet.get("rxSnr"),
            "rx_rssi": packet.get("rxRssi"),
            "hop_limit": packet.get("hopLimit"),
            "hop_start": packet.get("hopStart"),
            "packet_id": packet.get("id"),          # the radio's id for this packet, when it gave one
            "stamp": time.time(),                   # exact time, so two events in one second differ
        }
        return activity

    def connect(self):
        """Attempt to connect to Meshtastic device (call from background thread)"""
        if not bootstrap.MESHTASTIC_AVAILABLE:
            return

        with self.lock:
            if self.connecting or self.active:
                return
            self.connecting = True

        if bootstrap.MESHTASTIC_HOST:
            self.connection_status = f"Connecting to {bootstrap.MESHTASTIC_HOST}:{bootstrap.MESHTASTIC_PORT}..."
            debug(f"LoRa: TCP connect to {bootstrap.MESHTASTIC_HOST}:{bootstrap.MESHTASTIC_PORT}")
            try:
                with self.lock:
                    self.interface = bootstrap._create_tcp_interface(bootstrap.MESHTASTIC_HOST, bootstrap.MESHTASTIC_PORT)
                    self.active = True
                    self.connection_status = "Connected (TCP)"
                debug("LoRa: Meshtastic TCP connected successfully")
                log("LoRa: Meshtastic mesh connected (TCP)")
                self._listen()
            except Exception as e:
                debug(f"LoRa: TCP connection failed: {e}")
                log(f"LoRa: TCP connection failed - {str(e)[:40]}")
                self.connection_status = f"Failed (TCP): {explain_tcp_failure(e, bootstrap.MESHTASTIC_HOST, bootstrap.MESHTASTIC_PORT)}"
                with self.lock:
                    self.interface = None
                    self.active = False
            finally:
                with self.lock:
                    self.connecting = False
            return

        with self.lock:
            self.connection_status = "Checking for serial devices..."
        time.sleep(2)

        found_devices = [dev for dev in SERIAL_CANDIDATES if os.path.exists(dev)]

        # A Flipper or ESP32 board can enumerate as ttyACM0. Leave those alone
        # (aiov2_ctl --companions --json says which ports they are).
        companion_tty = companions.companion_ports(companions.get_companions())
        skipped = [d for d in found_devices if d in companion_tty]
        if skipped:
            found_devices = [d for d in found_devices if d not in skipped]
            log(f"LoRa: skipping companion port {', '.join(s.split('/')[-1] for s in skipped)}")

        if not found_devices:
            debug("LoRa: No serial devices found")
            log("LoRa: No USB serial devices detected")
            with self.lock:
                self.connection_status = "No serial devices found (power-cycle LoRa?)"
                self.connecting = False
            return

        debug(f"LoRa: Found devices: {found_devices}")
        port, why = choose_serial_port(found_devices, usb_identity)
        log(f"LoRa: {why}")
        if port is None:
            with self.lock:
                self.connection_status = why
                self.connecting = False
            return
        with self.lock:
            self.connection_status = f"Found: {why}"

        with self.lock:
            self.conflict_processes = self._detect_serial_conflicts([port])
        if self.conflict_processes:
            labels = ", ".join(f"{item['name']}:{item['pid']}" for item in self.conflict_processes[:3])
            with self.lock:
                self.connection_status = f"Serial busy: {labels}"
            debug(f"LoRa: serial conflict detected ({labels})")
            log(f"LoRa: serial busy - {labels}")
            with self.lock:
                self.connecting = False
            return

        with self.lock:
            self.connection_status = "Connecting to Meshtastic..."
        debug("LoRa: Attempting to connect to Meshtastic device...")

        try:
            with self.lock:
                # Always the chosen port by name: the library's auto-detection could open an unrelated device.
                self.interface = bootstrap.meshtastic.serial_interface.SerialInterface(devPath=port)
                self.active = True
                self.connection_status = f"Connected (serial: {why})"
            debug("LoRa: Meshtastic connected successfully")
            log("LoRa: Meshtastic mesh connected")
            self._listen()

        except Exception as e:
            debug(f"LoRa: connection failed: {e}")
            log(f"LoRa: Connection failed - {str(e)[:40]}")
            self.connection_status = f"Failed ({port.split('/')[-1]}): {str(e)[:50]}"
            with self.lock:
                self.interface = None
                self.active = False
        finally:
            with self.lock:
                self.connecting = False

    def _handle_packet(self, packet):
        """Handle incoming Meshtastic packet"""
        try:
            with self.lock:
                activity = self._build_activity(packet, sent=False)
                self._remember_link_metrics(packet.get("fromId"), packet)
                self._remember_channel_activity(activity["channel"], sent=False)
                self.last_rx_time = time.time()
                self.last_rx_summary = f"{activity['from']}: {activity['summary']}"

                if activity["kind"] in ("text", "checkin"):
                    self._append_bounded(
                        self.messages,
                        {
                            "time": activity["time"],
                            "from": activity["from"],
                            "text": activity["summary"],
                            "channel": activity["channel"],
                            "sent": False,
                        },
                        self.max_messages,
                    )
                self._append_activity(activity)
            if activity["kind"] in ("text", "checkin"):
                debug(f"LoRa RX: [{activity['from']}] {activity['summary']}")
                log(
                    f"LoRa: {activity['from']}: "
                    f"{activity['summary'][:30]}{'...' if len(activity['summary']) > 30 else ''}"
                )
            else:
                debug(f"LoRa RX {activity['portnum']}: {activity['summary']}")
                log(f"LoRa: {activity['from']} {activity['summary'][:40]}")
        except Exception as e:
            debug(f"LoRa: packet parse error: {e}")

    def refresh_nodes(self):
        """Refresh known node list from the interface."""
        with self.lock:
            if not self.active or not self.interface:
                return
            nodes = copy.deepcopy(getattr(self.interface, "nodes", None))
            try:
                self.configured_channels = configured_channels(
                    getattr(getattr(self.interface, "localNode", None), "channels", None))
            except Exception as e:                      # reading only; a library without it keeps the fallback
                debug(f"LoRa: could not read the configured channels: {e}")
                self.configured_channels = None
            try:
                self.modem_preset = modem_preset_name(getattr(getattr(getattr(
                    self.interface, "localNode", None), "localConfig", None), "lora", None))
            except Exception as e:
                debug(f"LoRa: could not read the modem preset: {e}")
                self.modem_preset = None
        if isinstance(nodes, dict) and nodes:
            merged_nodes = {}
            for node_id, info in nodes.items():
                key = str(node_id)
                with self.lock:
                    existing = self.node_info.get(key)
                merged = dict(existing) if isinstance(existing, dict) else {}
                if isinstance(info, dict):
                    for field, value in info.items():
                        if isinstance(value, dict) and isinstance(merged.get(field), dict):
                            merged[field].update(value)
                        else:
                            merged[field] = value
                    position = info.get("position")
                    if isinstance(position, dict):
                        self._remember_node_position(key, position)
                merged_nodes[key] = merged
            with self.lock:
                self.node_info.update(merged_nodes)

    def send_message(self, text, channel_index=None, want_ack=False):
        """Send a text message on the selected active channel. It is handed to the radio and nothing more is known,
        unless `want_ack` is True (the "Ask for acknowledgement" setting, off by default): then the library is asked
        for an acknowledgement, which makes the mesh retry and so the radio sends more, and the message's state
        follows the answer (ops_agent/delivery.py). With `want_ack` off the call to the library is exactly as before."""
        want_ack = delivery.want_ack_setting(want_ack)
        result = None
        with self.lock:
            active = self.active
            interface = self.interface
            if channel_index is None:
                selected_channel, _, _ = self._effective_selected_channel_locked()
            else:
                selected_channel = self._normalize_channel(channel_index)
        if not active or not interface:
            self._set_tx_status(False, "Send failed: not connected")
            log("LoRa: Cannot send - not connected")
            return False

        try:
            if want_ack:
                result = interface.sendText(text, channelIndex=selected_channel, wantAck=True)
            else:
                result = interface.sendText(text, channelIndex=selected_channel)
        except TypeError:
            if selected_channel != 0:
                self._set_tx_status(False, f"Send failed: channel {selected_channel} unsupported")
                log(f"LoRa: send API does not support ch{selected_channel}")
                return False
            try:
                result = interface.sendText(text)
                want_ack = False                 # a library this old was not asked for one
            except Exception as e:
                self._set_tx_status(False, f"Send failed: {str(e)[:40]}")
                self._append_activity(
                    {
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "from": "Me",
                        "from_id": "Me",
                        "channel": selected_channel,
                        "portnum": "TEXT_MESSAGE_APP",
                        "kind": "event",
                        "summary": f"TX failed: {str(e)[:32]}",
                        "sent": True,
                        "rx_snr": None,
                        "rx_rssi": None,
                        "hop_limit": None,
                        "hop_start": None,
                    }
                )
                debug(f"LoRa: send failed: {e}")
                log(f"LoRa: Send failed - {e}")
                return False
        except Exception as e:
            self._set_tx_status(False, f"Send failed: {str(e)[:40]}")
            self._append_activity(
                {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "from": "Me",
                    "from_id": "Me",
                    "channel": selected_channel,
                    "portnum": "TEXT_MESSAGE_APP",
                    "kind": "event",
                    "summary": f"TX failed: {str(e)[:32]}",
                    "sent": True,
                    "rx_snr": None,
                    "rx_rssi": None,
                    "hop_limit": None,
                    "hop_start": None,
                }
            )
            debug(f"LoRa: send failed: {e}")
            log(f"LoRa: Send failed - {e}")
            return False

        sent_id = delivery.packet_id(result)
        record = delivery.new_delivery(sent_id, want_ack, time.time())
        message = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "from": "Me",
            "text": text,
            "channel": selected_channel,
            "sent": True,
            "packet_id": sent_id,
            "delivery": record,
        }
        with self.lock:
            self._remember_channel_activity(selected_channel, sent=True)
            self._append_bounded(self.messages, message, self.max_messages)
            activity = self._build_activity(
                {
                    "fromId": "Me",
                    "id": sent_id,
                    "channel": selected_channel,
                    "decoded": {"text": text, "portnum": "TEXT_MESSAGE_APP"},
                },
                sent=True,
                text_override=text,
            )
            activity["delivery"] = record           # the same record: an answer shows wherever the message does
            self._append_activity(activity)
            if record["state"] == delivery.WAITING:
                self.deliveries[sent_id] = record
        self._set_tx_status(True, f"Handed to the radio, ch{selected_channel}: {text[:20]}"
                                  + (" (asking for an acknowledgement)" if record["state"] == delivery.WAITING else ""))

        debug(f"LoRa TX ch{selected_channel}: {text}")
        log(f"LoRa: Sent on ch{selected_channel} '{text[:30]}{'...' if len(text) > 30 else ''}'")
        return True

    def get_status(self):
        """Return status string"""
        if not bootstrap.MESHTASTIC_AVAILABLE:
            return "meshtastic library not installed"

        with self.lock:
            if self.connecting:
                return self.connection_status

            if not self.active:
                return self.connection_status

            selected_channel, best_channel, _ = self._effective_selected_channel_locked()
            status_bits = [f"{len(self.node_info)} nodes", f"{len(self.messages)} msgs"]
            if self.channel_activity:
                channel_bits = [f"ch{selected_channel}"]
                if self.auto_follow_channel:
                    channel_bits.append("auto")
                if self.channel_locked:
                    channel_bits.append("lock")
                if best_channel is not None and selected_channel != best_channel:
                    channel_bits.append(f"best{best_channel}")
                status_bits.append(" ".join(channel_bits))
            if self.last_rx_time:
                status_bits.append(f"RX {int(max(0, time.time() - self.last_rx_time))}s ago")
            if self.last_tx_status:
                status_bits.append("TX ok" if self.last_tx_ok else "TX failed")
        return "Connected | " + " | ".join(status_bits)
