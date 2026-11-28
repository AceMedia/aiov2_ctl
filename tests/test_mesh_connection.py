"""The mesh connection: hearing packets, knowing the connection has gone, recognising this radio's own node, and
keeping the channel the person chose. No radio and no Meshtastic library are needed: a stand-in bus and a
stand-in connection take their place."""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_agent import lora, nodeid  # noqa: E402
from ops_gui import lora_panel as lp, mesh_hub  # noqa: E402


class Bus:
    """What the library's bus does, as far as this code uses it: subscribe once per listener, send to all."""

    def __init__(self):
        self.listeners = {}

    def subscribe(self, listener, topic):
        if listener not in self.listeners.setdefault(topic, []):
            self.listeners[topic].append(listener)

    def unsubscribe(self, listener, topic):
        self.listeners[topic].remove(listener)

    def send(self, topic, **data):
        for listener in list(self.listeners.get(topic, [])):
            listener(**data)


class Link:
    def __init__(self, number=0xA1B2C3D4):
        self.myInfo = types.SimpleNamespace(my_node_num=number)
        self.nodes = {}
        self.closed = 0

    def close(self):
        self.closed += 1


def text_packet(number, text="hello", sender="!0000beef", channel=0):
    return {"from": 0xBEEF, "fromId": sender, "id": number, "channel": channel,
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": text}}


def engine(bus, link=None):
    e = lora.LoRa()
    e.interface = link or Link()
    e.active = True
    e.connection_status = "Connected (TCP)"
    with mock.patch.object(lora, "_pubsub", return_value=bus):
        e._listen()
    return e


class NodeIdentityTests(unittest.TestCase):
    def test_a_number_a_string_of_digits_and_a_name_are_all_written_one_way(self):
        self.assertEqual(nodeid.canonical(0xA1B2C3D4), "!a1b2c3d4")
        self.assertEqual(nodeid.canonical(str(0xA1B2C3D4)), "!a1b2c3d4")
        self.assertEqual(nodeid.canonical("!A1B2C3D4"), "!a1b2c3d4")
        self.assertEqual(nodeid.canonical(0xBEEF), "!0000beef")
        self.assertIsNone(nodeid.canonical(None))
        self.assertEqual(nodeid.canonical("^all"), "^all")

    def test_two_forms_of_one_node_are_the_same_and_nothing_matches_nothing(self):
        self.assertTrue(nodeid.same(0xA1B2C3D4, "!a1b2c3d4"))
        self.assertTrue(nodeid.same("2712847316", "!A1B2C3D4"))
        self.assertFalse(nodeid.same(0xA1B2C3D4, "!a1b2c3d5"))
        self.assertFalse(nodeid.same(None, None))
        self.assertFalse(nodeid.same(None, "!a1b2c3d4"))

    def test_the_engine_reports_its_own_node_the_way_the_node_list_names_nodes(self):
        e = engine(Bus())
        e.node_info["!a1b2c3d4"] = {"position": {"latitude": 51.5, "longitude": -0.12}}
        snap = e.snapshot()
        self.assertEqual(snap["self_id"], "!a1b2c3d4")
        self.assertIn(snap["self_id"], snap["node_info"], "the console looks its own node up by this key")

    def test_the_panel_finds_its_own_node_whichever_form_it_is_given(self):
        nodes = {"!a1b2c3d4": {"position": {"latitude": 51.5, "longitude": -0.12}}, "!0000beef": {}}
        for form in ("!a1b2c3d4", 0xA1B2C3D4, str(0xA1B2C3D4), "!A1B2C3D4"):
            self.assertIsNotNone(lp.own_position(nodes, form), form)
            rows = lp.node_rows(nodes, form)
            self.assertEqual([r["self"] for r in rows].count(True), 1, form)
        self.assertIsNone(lp.own_position(nodes, None))

    def test_with_no_connection_there_is_no_own_node(self):
        self.assertIsNone(lora.LoRa().snapshot()["self_id"])


class ReceivingTests(unittest.TestCase):
    def test_a_packet_published_by_the_library_reaches_the_engine(self):
        bus = Bus()
        e = engine(bus)
        bus.send("meshtastic.receive", packet=text_packet(1, "hello"), interface=e.interface)
        snap = e.snapshot()
        self.assertEqual([m["text"] for m in snap["messages"]], ["hello"])
        self.assertEqual(len(snap["activities"]), 1)
        self.assertIsNotNone(snap["last_rx_time"])

    def test_it_does_not_depend_on_a_hook_the_library_no_longer_has(self):
        link = Link()
        self.assertFalse(hasattr(link, "messageReceived"))
        bus = Bus()
        e = engine(bus, link)
        self.assertIn(e._on_receive, bus.listeners["meshtastic.receive"])

    def test_the_same_packet_counts_once_even_after_listening_twice(self):
        bus = Bus()
        e = engine(bus)
        with mock.patch.object(lora, "_pubsub", return_value=bus):
            e._listen()                                     # a reconnect subscribes again
        for _ in range(3):
            bus.send("meshtastic.receive", packet=text_packet(7), interface=e.interface)
        self.assertEqual(len(e.snapshot()["messages"]), 1)
        bus.send("meshtastic.receive", packet=text_packet(8, "again"), interface=e.interface)
        self.assertEqual(len(e.snapshot()["messages"]), 2)

    def test_another_connections_packets_are_not_this_ones(self):
        bus = Bus()
        e = engine(bus)
        bus.send("meshtastic.receive", packet=text_packet(1), interface=Link())
        self.assertEqual(e.snapshot()["messages"], [])

    def test_something_that_is_not_a_packet_is_ignored_without_an_error(self):
        bus = Bus()
        e = engine(bus)
        for bad in (None, "text", 5, []):
            bus.send("meshtastic.receive", packet=bad, interface=e.interface)
        self.assertEqual(e.snapshot()["messages"], [])

    def test_after_closing_nothing_more_arrives_and_the_connection_is_closed_once(self):
        bus = Bus()
        link = Link()
        e = engine(bus, link)
        e.close()
        self.assertEqual(bus.listeners["meshtastic.receive"], [])
        self.assertEqual(bus.listeners["meshtastic.connection.lost"], [])
        self.assertEqual(link.closed, 1)
        self.assertFalse(e.active)
        self.assertIsNone(e.interface)
        e.close()
        self.assertEqual(link.closed, 1, "closing twice is harmless")

    def test_without_the_bus_an_old_librarys_hook_is_still_used(self):
        link = Link()
        link.messageReceived = None
        e = lora.LoRa()
        e.interface, e.active = link, True
        with mock.patch.object(lora, "_pubsub", return_value=None):
            self.assertFalse(e._listen())
        link.messageReceived(text_packet(3, "old way"), link)
        self.assertEqual([m["text"] for m in e.snapshot()["messages"]], ["old way"])


class ConnectionLostTests(unittest.TestCase):
    def test_the_library_saying_the_connection_is_lost_is_believed(self):
        bus = Bus()
        e = engine(bus)
        bus.send("meshtastic.connection.lost", interface=e.interface)
        snap = e.snapshot()
        self.assertFalse(snap["active"])
        self.assertEqual(snap["connection_status"], "Connection lost")

    def test_another_connection_being_lost_is_not_this_one(self):
        bus = Bus()
        e = engine(bus)
        bus.send("meshtastic.connection.lost", interface=Link())
        self.assertTrue(e.snapshot()["active"])

    def test_only_a_connection_that_really_opened_is_handed_to_the_tabs(self):
        good = types.SimpleNamespace(active=True, connection_status="Connected (TCP)")
        self.assertEqual(mesh_hub.engine_result(good), {"hint": "", "lora": good})
        failed = types.SimpleNamespace(active=False, connection_status="Failed (TCP): meshtasticd is not running")
        result = mesh_hub.engine_result(failed)
        self.assertIsNone(result["lora"])
        self.assertIn("meshtasticd is not running", result["hint"])
        self.assertIn("not connected", result["hint"])
        vague = types.SimpleNamespace(active=False, connection_status="Not started")
        self.assertEqual(mesh_hub.engine_result(vague)["hint"], "Mesh not connected: The mesh did not answer.")
        self.assertIsNone(mesh_hub.engine_result(None)["lora"])


class ChosenChannelTests(unittest.TestCase):
    def heard(self, e, channel, ago=0):
        e.channel_activity[channel] = {"count": 3, "rx_count": 3, "tx_count": 0, "last_seen": lora.time.time() - ago}

    def test_a_chosen_channel_that_has_gone_quiet_stays_chosen(self):
        e = lora.LoRa()
        self.heard(e, 0)
        e.set_selected_channel(3)
        self.assertEqual(e.snapshot()["selected_channel"], 3, "it used to become 0, the busy one")
        self.assertEqual(e.selected_channel, 3)

    def test_locked_it_stays_even_with_follow_on(self):
        e = lora.LoRa()
        self.heard(e, 0)
        e.set_selected_channel(3)
        e.auto_follow_channel, e.channel_locked = True, True
        self.assertEqual(e.snapshot()["selected_channel"], 3)

    def test_follow_without_the_lock_moves_to_the_busiest(self):
        e = lora.LoRa()
        self.heard(e, 2)
        e.set_selected_channel(3)
        e.auto_follow_channel = True
        snap = e.snapshot()
        self.assertEqual(snap["selected_channel"], 2)
        self.assertEqual(snap["best_channel"], 2)

    def test_hearing_traffic_on_another_channel_does_not_move_it(self):
        e = lora.LoRa()
        e.set_selected_channel(3)
        e._handle_packet(text_packet(1, channel=1))
        self.assertEqual(e.snapshot()["selected_channel"], 3)


if __name__ == "__main__":
    unittest.main()


class ChatIsKeptApartTests(unittest.TestCase):
    """A burst of telemetry must not push a text message out of what the Messages view shows."""

    def test_a_message_survives_300_telemetry_packets(self):
        engine = lora.LoRa()
        engine._handle_packet({"fromId": "!0000abcd", "channel": 0,
                               "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "meet at the hut"}})
        for i in range(300):
            engine._handle_packet({"fromId": "!0000beef", "channel": 0,
                                   "decoded": {"portnum": "TELEMETRY_APP",
                                               "telemetry": {"batteryLevel": 50 + i % 40}}})
        snap = engine.snapshot()
        self.assertEqual(snap["activities"][-1]["kind"], "telemetry")
        self.assertNotIn("text", [a["kind"] for a in snap["activities"]])      # long gone from the activity list
        rows = lp.message_rows(lp.chat_events(snap))
        self.assertEqual([r["text"] for r in rows], ["meet at the hut"])

    def test_the_chat_list_is_bounded(self):
        engine = lora.LoRa()
        for i in range(engine.max_chat + 25):
            engine._handle_packet({"fromId": "!0000abcd", "channel": 0,
                                   "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": f"m{i}"}})
        chat = engine.snapshot()["chat"]
        self.assertEqual(len(chat), engine.max_chat)
        self.assertEqual(chat[-1]["summary"], f"m{engine.max_chat + 24}")

    def test_an_older_snapshot_without_chat_still_works(self):
        acts = [{"kind": "text", "time": "t", "from": "Ann", "summary": "hi"}]
        self.assertEqual(lp.chat_events({"activities": acts}), acts)
        self.assertEqual(lp.chat_events(None), [])


def channel(index, role, name=""):
    """A stand-in for one of the library's channel objects: index, role (0 disabled, 1 primary, 2 secondary) and
    settings.name."""
    return types.SimpleNamespace(index=index, role=role, settings=types.SimpleNamespace(name=name))


class ConfiguredChannelTests(unittest.TestCase):
    CHANNELS = [channel(0, 1, ""), channel(1, 2, "Family"), channel(2, 0, "Old"), channel(3, 2, "Hikers")]

    def test_the_radios_channels_become_the_list_primary_first_disabled_left_out(self):
        got = lora.configured_channels(list(reversed(self.CHANNELS)))
        self.assertEqual([{k: c[k] for k in ("index", "name", "primary", "key")} for c in got],
                         [{"index": 0, "name": "", "primary": True, "key": "unknown"},
                          {"index": 1, "name": "Family", "primary": False, "key": "unknown"},
                          {"index": 3, "name": "Hikers", "primary": False, "key": "unknown"}])
        self.assertTrue(all(c["uplink"] is False and c["downlink"] is False for c in got))

    def test_nothing_usable_means_none(self):
        for bad in (None, [], [channel(0, 0)], 5, [object()]):
            self.assertIsNone(lora.configured_channels(bad), bad)

    def test_the_send_on_list_uses_them_and_says_the_default_name(self):
        snap = {"configured_channels": lora.configured_channels(self.CHANNELS), "selected_channel": 1,
                "active_channels": [{"channel": 5, "count": 9}]}
        self.assertEqual(lp.channel_choices(snap),
                         [(0, "0: default name (primary)"), (1, "1: Family"), (3, "3: Hikers")])

    def test_a_selected_channel_the_radio_does_not_have_is_still_listed_and_said_so(self):
        snap = {"configured_channels": [{"index": 0, "name": "LongFast", "primary": True}], "selected_channel": 4}
        self.assertEqual(lp.channel_choices(snap),
                         [(0, "0: LongFast (primary)"), (4, "Channel 4 (not set up on this radio)")])

    def test_without_them_the_list_is_built_from_traffic_as_before(self):
        snap = {"configured_channels": None, "active_channels": [{"channel": 2, "count": 5}], "selected_channel": 0}
        self.assertEqual([c for c, _ in lp.channel_choices(snap)], [0, 2])

    def test_the_engine_reads_them_from_the_connection_and_changes_nothing(self):
        link = Link()
        link.localNode = types.SimpleNamespace(channels=list(self.CHANNELS))
        e = engine(Bus(), link)
        e.refresh_nodes()
        self.assertEqual([c["index"] for c in e.snapshot()["configured_channels"]], [0, 1, 3])
        self.assertEqual([(c.index, c.role, c.settings.name) for c in link.localNode.channels],
                         [(0, 1, ""), (1, 2, "Family"), (2, 0, "Old"), (3, 2, "Hikers")])

    def test_a_connection_without_them_keeps_the_fallback(self):
        e = engine(Bus())
        e.refresh_nodes()
        self.assertIsNone(e.snapshot()["configured_channels"])
