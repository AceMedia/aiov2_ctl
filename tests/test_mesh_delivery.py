"""What became of a sent message: handed to the radio, and, only when the person asked for it, acknowledged, failed
or not acknowledged. Fake packets go through the same stand-in bus as the connection tests; no radio is needed."""
import inspect
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_agent import delivery, lora  # noqa: E402
from ops_gui import lora_panel as lp, settings_ui  # noqa: E402
from tests.test_mesh_connection import Bus, Link, engine  # noqa: E402


class Radio(Link):
    """A stand-in connection that records every sendText call and returns a packet with an id, as the library does."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self.next_id = 1000

    def sendText(self, text, **kwargs):
        self.calls.append((text, kwargs))
        self.next_id += 1
        return types.SimpleNamespace(id=self.next_id)


def routing(request_id, reason="NONE"):
    routing_part = {} if reason is None else {"errorReason": reason}
    return {"from": 1, "id": 77000 + request_id, "decoded": {"portnum": "ROUTING_APP", "requestId": request_id,
                                                              "routing": routing_part}}


def sent_state(e):
    snap = e.snapshot()
    return [r["state"] for r in lp.message_rows(lp.chat_events(snap)) if r["sent"]]


class PureStateTests(unittest.TestCase):
    def test_the_packet_id_is_read_from_an_object_or_a_dict(self):
        self.assertEqual(delivery.packet_id(types.SimpleNamespace(id=42)), 42)
        self.assertEqual(delivery.packet_id({"id": 7}), 7)
        for bad in (None, object(), {"id": 0}, {"id": "x"}):
            self.assertIsNone(delivery.packet_id(bad))

    def test_without_asking_it_is_only_handed_to_the_radio(self):
        d = delivery.new_delivery(42, False, 100)
        self.assertEqual(delivery.delivery_text(d), "handed to the radio")
        self.assertIs(delivery.after_routing(d, "NONE"), d, "an answer does not change one that did not ask")
        self.assertIs(delivery.after_time(d, 1000), d)

    def test_asking_waits_then_is_acknowledged_or_fails_with_the_reason(self):
        d = delivery.new_delivery(42, True, 100)
        self.assertEqual(d["state"], delivery.WAITING)
        self.assertEqual(delivery.delivery_text(delivery.after_routing(d, "NONE")), "acknowledged")
        self.assertEqual(delivery.delivery_text(delivery.after_routing(d, "MAX_RETRANSMIT")),
                         "failed: MAX_RETRANSMIT")

    def test_no_answer_within_a_minute_is_no_acknowledgement_and_a_late_one_still_counts(self):
        d = delivery.new_delivery(42, True, 100)
        self.assertEqual(delivery.after_time(d, 159)["state"], delivery.WAITING)
        late = delivery.after_time(d, 160)
        self.assertEqual(delivery.delivery_text(late), "no acknowledgement")
        self.assertEqual(delivery.after_routing(late, "NONE")["state"], delivery.ACKED)
        self.assertEqual(delivery.ACK_TIMEOUT_SECONDS, 60)

    def test_asking_without_a_packet_id_cannot_be_followed(self):
        self.assertEqual(delivery.new_delivery(None, True, 100)["state"], delivery.HANDED)

    def test_routing_replies(self):
        self.assertEqual(delivery.routing_reply(routing(5)), (5, "NONE"))
        self.assertEqual(delivery.routing_reply(routing(5, None)), (5, "NONE"), "a default reason is left out")
        self.assertEqual(delivery.routing_reply(routing(5, "NO_ROUTE")), (5, "NO_ROUTE"))
        for bad in (None, {}, {"decoded": {"routing": {}}}, {"decoded": {"requestId": 5}}, "x"):
            self.assertIsNone(delivery.routing_reply(bad))

    def test_the_setting_is_on_only_when_switched_on(self):
        self.assertTrue(delivery.want_ack_setting(True))
        for value in (None, False, "yes", 1):
            self.assertFalse(delivery.want_ack_setting(value))


class EngineTests(unittest.TestCase):
    def test_by_default_the_call_to_the_library_is_exactly_as_before(self):
        radio = Radio()
        e = engine(Bus(), radio)
        self.assertTrue(e.send_message("hello", channel_index=0))
        self.assertEqual(radio.calls, [("hello", {"channelIndex": 0})], "no wantAck: nothing more goes on the air")
        self.assertEqual(sent_state(e), ["handed to the radio"])
        self.assertEqual(e.snapshot()["messages"][-1]["packet_id"], 1001, "the packet's id is kept")
        self.assertEqual(e.deliveries, {})

    def test_an_answer_does_not_change_a_message_that_did_not_ask(self):
        bus, radio = Bus(), Radio()
        e = engine(bus, radio)
        e.send_message("hello", channel_index=0)
        bus.send("meshtastic.receive.routing", packet=routing(1001, "MAX_RETRANSMIT"), interface=radio)
        self.assertEqual(sent_state(e), ["handed to the radio"])

    def test_asked_for_it_is_acknowledged_when_the_library_says_so(self):
        bus, radio = Bus(), Radio()
        e = engine(bus, radio)
        e.send_message("hello", channel_index=2, want_ack=True)
        self.assertEqual(radio.calls, [("hello", {"channelIndex": 2, "wantAck": True})])
        self.assertEqual(sent_state(e), ["handed to the radio, waiting for an acknowledgement"])
        bus.send("meshtastic.receive.routing", packet=routing(999), interface=radio)        # someone else's
        bus.send("meshtastic.receive.routing", packet=routing(1001), interface=Link())       # another connection
        self.assertEqual(sent_state(e), ["handed to the radio, waiting for an acknowledgement"])
        bus.send("meshtastic.receive.routing", packet=routing(1001), interface=radio)
        self.assertEqual(sent_state(e), ["acknowledged"])
        self.assertEqual(e.snapshot()["messages"][-1]["delivery"]["state"], delivery.ACKED)
        self.assertIn("[acknowledged]", lp.activity_line(e.snapshot()["chat"][-1]))

    def test_a_failure_shows_the_reason(self):
        bus, radio = Bus(), Radio()
        e = engine(bus, radio)
        e.send_message("hello", channel_index=0, want_ack=True)
        bus.send("meshtastic.receive.routing", packet=routing(1001, "NO_RESPONSE"), interface=radio)
        self.assertEqual(sent_state(e), ["failed: NO_RESPONSE"])

    def test_no_answer_within_a_minute(self):
        bus, radio = Bus(), Radio()
        e = engine(bus, radio)
        e.send_message("hello", channel_index=0, want_ack=True)
        record = e.deliveries[1001]
        record["sent_at"] -= 61
        self.assertEqual(sent_state(e), ["no acknowledgement"])
        bus.send("meshtastic.receive.routing", packet=routing(1001), interface=radio)
        self.assertEqual(sent_state(e), ["acknowledged"], "a late acknowledgement still counts")

    def test_closing_stops_listening_for_answers(self):
        bus = Bus()
        e = engine(bus, Radio())
        e.close()
        self.assertEqual(bus.listeners["meshtastic.receive.routing"], [])


class PanelAndSettingTests(unittest.TestCase):
    def test_the_view_is_redrawn_when_a_state_changes(self):
        row = {"kind": "text", "sent": True, "summary": "hi", "time": "t", "packet_id": 5,
               "delivery": delivery.new_delivery(5, True, 0)}
        before = lp.view_signature(lp.message_rows([row]))
        row["delivery"] = delivery.after_routing(row["delivery"], "NONE")
        self.assertNotEqual(before, lp.view_signature(lp.message_rows([row])))

    def test_the_tab_never_says_just_sent(self):
        self.assertIn("Handed to the radio", lp.sent_note(True, False))
        self.assertIn("acknowledgement", lp.sent_note(True, True))
        self.assertIn("failed", lp.sent_note(False, False))
        self.assertNotIn('"Sent."', inspect.getsource(lp))

    def test_the_tab_passes_the_setting_and_the_setting_says_it_sends_more(self):
        self.assertIn('send_message(text, want_ack=want_ack)', inspect.getsource(lp.build_panel))
        self.assertIn('win.flag("mesh_want_ack")', inspect.getsource(lp.build_panel))
        src = inspect.getsource(settings_ui)
        self.assertIn('"Ask for acknowledgement", MESH_ACK_HINT', src)
        self.assertIn('win.set_flag("mesh_want_ack"', src)
        self.assertIn("Off unless you switch it on", settings_ui.MESH_ACK_HINT)
        self.assertIn("retries", settings_ui.MESH_ACK_HINT)
        self.assertIn("the radio sends more", settings_ui.MESH_ACK_HINT)


if __name__ == "__main__":
    unittest.main()
