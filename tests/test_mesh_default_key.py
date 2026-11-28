"""The standing warning when the channel being sent on can be read by anyone nearby: not encrypted, Meshtastic's
public default key, or a simple variant of it. Stand-in channel objects take the place of the library's; nothing
about the radio is changed."""
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


def kind(*args, **kw):
    return lora.channel_key_kind(*args, **kw)


class KeyKindTests(unittest.TestCase):
    def test_an_empty_key_means_not_encrypted(self):
        self.assertEqual(kind(settings(name="", psk=b""), primary=True), "none")
        self.assertEqual(kind(settings(name="Family", psk=b"")), "none")

    def test_a_one_byte_key_of_1_is_the_default(self):
        self.assertEqual(kind(settings(name="", psk=b"\x01"), primary=True), "default")
        self.assertEqual(kind(settings(name="Family", psk=b"\x01")), "default")

    def test_one_byte_keys_2_to_10_are_public_variants(self):
        for n in range(2, 11):
            self.assertEqual(kind(settings(name="Hill", psk=bytes([n]))), "public_variant", n)

    def test_16_and_32_byte_keys_are_private(self):
        self.assertEqual(kind(settings(name="", psk=bytes(range(16))), primary=True), "private")
        self.assertEqual(kind(settings(name="Family", psk=bytes(range(32)))), "private")

    def test_anything_else_is_unknown(self):
        for psk in (b"\x00", b"\x0b", b"\xff", b"ab", bytes(15), bytes(17), bytes(31), bytes(33)):
            self.assertEqual(kind(settings(name="Hill", psk=psk)), "unknown", psk)
        self.assertEqual(kind(settings(psk=object())), "unknown")
        self.assertEqual(kind(settings(psk=5)), "unknown")
        self.assertEqual(kind(None), "unknown")

    def test_without_a_key_an_unnamed_primary_on_longfast_counts_and_nothing_else_is_guessed(self):
        self.assertEqual(kind(settings(name=""), primary=True, modem_preset="LongFast"), "default")
        self.assertEqual(kind(settings(name=""), primary=True, modem_preset="MediumFast"), "unknown")
        self.assertEqual(kind(settings(name=""), primary=True), "unknown")
        self.assertEqual(kind(settings(name=""), primary=False, modem_preset="LongFast"), "unknown")
        self.assertEqual(kind(settings(name="Hill"), primary=True, modem_preset="LongFast"), "unknown")


class KeyLineTests(unittest.TestCase):
    SNAP = {"selected_channel": 0, "configured_channels": [
        {"index": 0, "name": "", "primary": True, "key": "default"},
        {"index": 1, "name": "Family", "primary": False, "key": "private"},
        {"index": 2, "name": "Odd", "primary": False, "key": "unknown"},
        {"index": 3, "name": "Open", "primary": False, "key": "none"},
        {"index": 4, "name": "Hill", "primary": False, "key": "public_variant"}]}

    def line(self, sel):
        return lp.key_line(dict(self.SNAP, selected_channel=sel))

    def test_each_readable_kind_says_what_is_true(self):
        self.assertEqual(self.line(3), "This channel is not encrypted: anyone with a Meshtastic radio nearby can "
                                       "read what is sent on it.")
        self.assertEqual(self.line(0), "This channel uses Meshtastic's public default key: anyone with a Meshtastic "
                                       "radio nearby can read what is sent on it.")
        self.assertEqual(self.line(4), "This channel uses a simple variant of Meshtastic's public default key, "
                                       "which is just as easy to read as the default.")

    def test_private_unknown_and_missing_channels_say_nothing(self):
        self.assertEqual(self.line(1), "")
        self.assertEqual(self.line(2), "")
        self.assertEqual(self.line(5), "")

    def test_no_channel_list_means_nothing_said(self):
        self.assertEqual(lp.key_line({"selected_channel": 0, "configured_channels": None}), "")
        self.assertEqual(lp.key_line(None), "")

    def test_the_engine_reads_the_key_and_changes_nothing(self):
        link = Link()
        chans = [channel(0, 1, name="", psk=b"\x01"), channel(1, 2, name="Family", psk=b"secret-key-16byt"),
                 channel(2, 2, name="Open", psk=b""), channel(3, 2, name="Hill", psk=b"\x04")]
        link.localNode = types.SimpleNamespace(channels=chans)
        e = engine(Bus(), link)
        e.refresh_nodes()
        snap = e.snapshot()
        self.assertEqual([c["key"] for c in snap["configured_channels"]],
                         ["default", "private", "none", "public_variant"])
        self.assertEqual(lp.key_line(snap), lp.KEY_WARNINGS["default"])
        self.assertEqual([c.settings.psk for c in chans], [b"\x01", b"secret-key-16byt", b"", b"\x04"])


if __name__ == "__main__":
    unittest.main()
