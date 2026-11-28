"""Broadcast or direct, said plainly: where each message went, and where the one being typed will go. An encrypted
packet's content is never shown. No radio and no Meshtastic library are needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_agent import lora  # noqa: E402
from ops_gui import lora_panel as lp  # noqa: E402
from tests.test_mesh_connection import Bus, Link, engine  # noqa: E402

ME = "!a1b2c3d4"


class DestinationTests(unittest.TestCase):
    def test_the_broadcast_address_in_either_form_is_everyone(self):
        for to in ("^all", 0xFFFFFFFF, "4294967295", "!ffffffff"):
            self.assertEqual(lp.destination(to, ME), "everyone", to)

    def test_this_radio_another_node_and_unknown(self):
        self.assertEqual(lp.destination(0xA1B2C3D4, ME), "me")
        self.assertEqual(lp.destination("!0000beef", ME), "other")
        self.assertEqual(lp.destination("!0000beef", None), "other")
        self.assertIsNone(lp.destination(None, ME))
        self.assertIsNone(lp.destination("", ME))

    def test_in_words_with_the_overheard_ones_labelled(self):
        nodes = {"!0000beef": {"user": {"longName": "Ann"}}}
        self.assertEqual(lp.destination_text("^all", ME), "to everyone on the channel")
        self.assertEqual(lp.destination_text(ME, ME), "direct to this radio")
        self.assertEqual(lp.destination_text(0xBEEF, ME, nodes), "direct to Ann, overheard")
        self.assertEqual(lp.destination_text("!00000099", ME, nodes), "direct to !00000099, overheard")
        self.assertEqual(lp.destination_text("!0000beef", ME, nodes, sent=True), "direct to Ann")
        self.assertEqual(lp.destination_text(None, ME), "")

    def test_an_activity_line_says_where_and_never_shows_encrypted_content(self):
        line = lp.activity_line({"time": "t", "channel": 0, "from": "Bob", "summary": "hi", "to_id": "^all"}, ME)
        self.assertIn("Bob (to everyone on the channel): hi", line)
        line = lp.activity_line({"from": "Bob", "kind": "encrypted", "summary": "\x8f\x01junk", "to_id": "!0000beef"}, ME)
        self.assertIn("overheard", line)
        self.assertIn("encrypted to them and cannot be read here", line)
        self.assertNotIn("junk", line)
        line = lp.activity_line({"from": "Bob", "kind": "encrypted", "summary": "x", "to_id": "^all"}, ME)
        self.assertIn("key this radio does not have", line)
        self.assertEqual(lp.activity_line({"time": "t", "channel": 0, "from": "Bob", "summary": "hi"}),
                         "t  ch0  < Bob: hi")

    def test_message_rows_carry_the_destination(self):
        rows = lp.message_rows([{"kind": "text", "summary": "hi", "to_id": "!0000beef"}])
        self.assertEqual(rows[0]["to_id"], "!0000beef")

    def test_the_composer_line(self):
        snap = {"selected_channel": 0, "modem_preset": "LongFast",
                "configured_channels": [{"index": 0, "name": "", "primary": True},
                                        {"index": 1, "name": "Family", "primary": False}]}
        self.assertEqual(lp.composer_line(snap), "To everyone on channel 0 (LongFast)")
        self.assertEqual(lp.composer_line(dict(snap, selected_channel=1)), "To everyone on channel 1 (Family)")
        self.assertEqual(lp.composer_line(dict(snap, modem_preset=None)), "To everyone on channel 0")
        self.assertEqual(lp.composer_line({"selected_channel": 3}), "To everyone on channel 3")
        self.assertEqual(lp.composer_line(None), "To everyone on channel 0")

    def test_the_preset_is_read_only_when_the_radio_uses_one(self):
        self.assertEqual(lora.modem_preset_name(types.SimpleNamespace(use_preset=True, modem_preset=0)), "LongFast")
        self.assertEqual(lora.modem_preset_name(types.SimpleNamespace(use_preset=True, modem_preset=4)), "MediumFast")
        self.assertIsNone(lora.modem_preset_name(types.SimpleNamespace(use_preset=False, modem_preset=0)))
        self.assertIsNone(lora.modem_preset_name(types.SimpleNamespace(use_preset=True, modem_preset=99)))
        self.assertIsNone(lora.modem_preset_name(None))

    def test_the_engine_keeps_the_destination_and_hides_encrypted_content(self):
        bus, link = Bus(), Link()
        e = engine(bus, link)
        bus.send("meshtastic.receive", interface=link, packet={
            "from": 0xBEEF, "fromId": "!0000beef", "to": 0x1234, "toId": "!00001234", "id": 9, "encrypted": b"\x8f junk"})
        bus.send("meshtastic.receive", interface=link, packet={
            "from": 0xBEEF, "fromId": "!0000beef", "to": 0xFFFFFFFF, "toId": "^all", "id": 10,
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hello"}})
        acts = e.snapshot()["activities"]
        self.assertEqual(acts[0]["kind"], "encrypted")
        self.assertEqual(acts[0]["to_id"], "!00001234")
        self.assertNotIn("junk", acts[0]["summary"])
        self.assertEqual(lp.destination(acts[1]["to_id"], ME), "everyone")
        self.assertEqual([a["kind"] for a in e.snapshot()["chat"]], ["text"])


if __name__ == "__main__":
    unittest.main()
