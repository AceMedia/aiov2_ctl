"""A routing packet is published on "meshtastic.receive.routing", and the library's bus also hands every message to
the listeners of the topics above it, so the engine's "meshtastic.receive" listener hears it too. An answer to one of
this app's own messages must only change that message's state; anyone else's routing packet is left as before."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_mesh_connection import Bus, engine  # noqa: E402
from tests.test_mesh_delivery import Radio, routing, sent_state  # noqa: E402


class TopicTreeBus(Bus):
    """As the real bus does: a message goes to the topic's own listeners and then to those of each topic above it."""

    def __init__(self, specific_first=True):
        super().__init__()
        self.specific_first = specific_first

    def send(self, topic, **data):
        parts = topic.split(".")
        topics = [".".join(parts[:n]) for n in range(len(parts), 0, -1)]
        if not self.specific_first:
            topics.reverse()
        for name in topics:
            super().send(name, **data)


class RoutingAnswerTests(unittest.TestCase):
    def check_answer_only_changes_the_state(self, specific_first):
        bus, radio = TopicTreeBus(specific_first), Radio()
        e = engine(bus, radio)
        e.send_message("hello", channel_index=0, want_ack=True)
        before = e.snapshot()
        bus.send("meshtastic.receive.routing", packet=routing(1001), interface=radio)
        after = e.snapshot()
        self.assertEqual(sent_state(e), ["acknowledged"])
        self.assertEqual(len(after["activities"]), len(before["activities"]), "no Routing line in Activity")
        self.assertEqual(len(after["chat"]), len(before["chat"]))
        self.assertEqual(after["channel_activity"], before["channel_activity"], "not counted as channel traffic")
        self.assertIsNone(after["last_rx_time"])

    def test_an_answer_to_our_message_only_changes_its_state(self):
        self.check_answer_only_changes_the_state(True)

    def test_the_same_whichever_listener_hears_it_first(self):
        self.check_answer_only_changes_the_state(False)

    def test_a_routing_packet_that_is_not_an_answer_to_ours_is_shown_as_before(self):
        bus, radio = TopicTreeBus(), Radio()
        e = engine(bus, radio)
        bus.send("meshtastic.receive.routing", packet=routing(555), interface=radio)
        self.assertEqual(len(e.snapshot()["activities"]), 1)


if __name__ == "__main__":
    unittest.main()

