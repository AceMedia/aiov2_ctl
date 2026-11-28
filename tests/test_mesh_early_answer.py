"""A sent message whose answer comes back before send_message() has finished registering it. The library's reader
thread publishes the routing answer on its own thread, so it can arrive while sendText() is still returning."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_agent import delivery, lora  # noqa: E402
from tests.test_mesh_connection import Bus, Link, engine  # noqa: E402


class QuickLink(Link):
    """A connection whose radio answers before sendText() returns."""

    def __init__(self, bus, reason="NONE"):
        super().__init__()
        self.bus, self.reason = bus, reason

    def sendText(self, text, channelIndex=0, wantAck=False):
        answer = {"decoded": {"requestId": 4242, "routing": {"errorReason": self.reason}}}
        self.bus.send("meshtastic.receive.routing", packet=answer, interface=self)
        return {"id": 4242}


class EarlyAnswerTests(unittest.TestCase):
    def sent_state(self, reason):
        bus = Bus()
        link = QuickLink(bus, reason)
        e = engine(bus, link)
        self.assertTrue(e.send_message("hi", want_ack=True))
        return e.snapshot()["messages"][-1]["delivery"]["state"]

    def test_an_acknowledgement_that_beats_the_send_still_counts(self):
        self.assertEqual(self.sent_state("NONE"), delivery.ACKED)

    def test_a_failure_that_beats_the_send_still_counts(self):
        self.assertEqual(self.sent_state("NO_CHANNEL"), delivery.FAILED)

    def test_without_an_acknowledgement_asked_for_an_answer_changes_nothing(self):
        bus = Bus()
        e = engine(bus, QuickLink(bus))
        self.assertTrue(e.send_message("hi"))
        self.assertEqual(e.snapshot()["messages"][-1]["delivery"]["state"], delivery.HANDED)


if __name__ == "__main__":
    unittest.main()

