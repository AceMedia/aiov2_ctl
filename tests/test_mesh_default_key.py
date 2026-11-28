"""The standing warning when the channel being sent on uses Meshtastic's public default key. Stand-in channel objects
take the place of the library's; nothing about the radio is changed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_agent import lora  # noqa: E402
from ops_gui import lora_panel as lp  # noqa: E402
from tests.test_mesh_connection import Bus, Link, engine  # noqa: E402


def settings(**kw):
    return types.SimpleNamespace(**kw)


def channel(index, role, **kw):
    return types.SimpleNamespace(index=index, role=role, settings=settings(**kw))


class DefaultKeyTests(unittest.TestCase):
    def test_a_one_byte_key_of_1_is_the_default(self):
        self.assertTrue(lora.uses_default_key(settings(name="", psk=b"\x01"), primary=True))
        self.assertTrue(lora.uses_default_key(settings(name="Family", psk=b"\x01")))

    def test_other_keys_are_not(self):
        self.assertFalse(lora.uses_default_key(settings(name="", psk=bytes(range(32))), primary=True))
        self.assertFalse(lora.uses_default_key(settings(name="", psk=b"\x02"), primary=True))
        self.assertFalse(lora.uses_default_key(settings(name="", psk=b""), primary=True))

    def test_without_a_key_an_unnamed_primary_on_longfast_counts_and_nothing_else_is_guessed(self):
        self.assertTrue(lora.uses_default_key(settings(name=""), primary=True, modem_preset="LongFast"))
        self.assertIsNone(lora.uses_default_key(settings(name=""), primary=True, modem_preset="MediumFast"))
        self.assertIsNone(lora.uses_default_key(settings(name=""), primary=True))
        self.assertIsNone(lora.uses_default_key(settings(name=""), primary=False, modem_preset="LongFast"))
        self.assertIsNone(lora.uses_default_key(settings(name="Hill"), primary=True, modem_preset="LongFast"))
        self.assertIsNone(lora.uses_default_key(None))
        self.assertIsNone(lora.uses_default_key(settings(psk=object())))

    def test_the_line_shows_only_for_the_channel_being_sent_on(self):
        snap = {"selected_channel": 0, "configured_channels": [
            {"index": 0, "name": "", "primary": True, "default_key": True},
            {"index": 1, "name": "Family", "primary": False, "default_key": False},
            {"index": 2, "name": "Odd", "primary": False, "default_key": None}]}
        self.assertEqual(lp.default_key_line(snap), lp.DEFAULT_KEY_WARNING)
        self.assertIn("anyone with a Meshtastic radio nearby can read", lp.DEFAULT_KEY_WARNING)
        self.assertEqual(lp.default_key_line(dict(snap, selected_channel=1)), "")
        self.assertEqual(lp.default_key_line(dict(snap, selected_channel=2)), "")
        self.assertEqual(lp.default_key_line(dict(snap, selected_channel=5)), "")

    def test_no_channel_list_means_nothing_said(self):
        self.assertEqual(lp.default_key_line({"selected_channel": 0, "configured_channels": None}), "")
        self.assertEqual(lp.default_key_line(None), "")

    def test_the_engine_reads_the_key_and_changes_nothing(self):
        link = Link()
        chans = [channel(0, 1, name="", psk=b"\x01"), channel(1, 2, name="Family", psk=b"secret-key-16byt")]
        link.localNode = types.SimpleNamespace(channels=chans)
        e = engine(Bus(), link)
        e.refresh_nodes()
        snap = e.snapshot()
        self.assertEqual([c["default_key"] for c in snap["configured_channels"]], [True, False])
        self.assertEqual(lp.default_key_line(snap), lp.DEFAULT_KEY_WARNING)
        self.assertEqual([c.settings.psk for c in chans], [b"\x01", b"secret-key-16byt"])


if __name__ == "__main__":
    unittest.main()
