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

    def test_every_one_byte_key_from_2_up_is_a_public_variant(self):
        for n in list(range(2, 11)) + [11, 73, 200, 255]:
            self.assertEqual(kind(settings(name="Hill", psk=bytes([n]))), "public_variant", n)

    def test_16_and_32_byte_keys_are_private(self):
        self.assertEqual(kind(settings(name="", psk=bytes(range(16))), primary=True), "private")
        self.assertEqual(kind(settings(name="Family", psk=bytes(range(32)))), "private")

    def test_anything_else_is_unknown(self):
        for psk in (b"ab", bytes(15), bytes(17), bytes(31), bytes(33)):
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


class SingleZeroByteTests(unittest.TestCase):
    """Meshtastic's channel settings define a one-byte key of 0 as no encryption at all. Seen on a real radio,
    where it was reported as "unknown" and so no warning was shown."""

    def test_a_single_zero_byte_is_no_encryption(self):
        settings = types.SimpleNamespace(psk=b"\x00", name="")
        self.assertEqual(lora.channel_key_kind(settings, True, "LongFast"), lora.KEY_NONE)

    def test_the_neighbouring_values_are_unchanged(self):
        self.assertEqual(lora.channel_key_kind(types.SimpleNamespace(psk=b"", name="")), lora.KEY_NONE)
        self.assertEqual(lora.channel_key_kind(types.SimpleNamespace(psk=b"\x01", name="")), lora.KEY_DEFAULT)
        self.assertEqual(lora.channel_key_kind(types.SimpleNamespace(psk=b"\x0b", name="")), lora.KEY_PUBLIC_VARIANT)
        self.assertEqual(lora.channel_key_kind(types.SimpleNamespace(psk=bytes([73]), name="")), lora.KEY_PUBLIC_VARIANT,
                         "the value seen on a real radio")
        self.assertEqual(lora.channel_key_kind(types.SimpleNamespace(psk=b"\x00\x00", name="")), lora.KEY_UNKNOWN)



class MqttLineTests(unittest.TestCase):
    """The radio's own MQTT module can pass a channel to an internet server. The tab says so; it changes nothing."""

    def snap(self, mqtt, uplink, downlink=False, sel=0):
        return {"mqtt_enabled": mqtt, "selected_channel": sel,
                "configured_channels": [{"index": 0, "name": "", "primary": True, "key": "public_variant",
                                         "uplink": uplink, "downlink": downlink}]}

    def test_it_is_said_when_the_module_is_on_and_the_channel_is_passed_up(self):
        line = lp.mqtt_line(self.snap(True, True))
        self.assertIn("internet server (MQTT)", line)
        self.assertIn("do not stay on the radio mesh", line)
        self.assertNotIn("takes in", line)
        self.assertIn("takes in messages from that server", lp.mqtt_line(self.snap(True, True, True)))

    def test_nothing_is_said_when_it_is_off_not_passed_up_or_not_reported(self):
        self.assertEqual(lp.mqtt_line(self.snap(False, True)), "")
        self.assertEqual(lp.mqtt_line(self.snap(True, False, True)), "")
        self.assertEqual(lp.mqtt_line(self.snap(None, True)), "")
        self.assertEqual(lp.mqtt_line(self.snap(True, True, sel=3)), "", "another channel is the one being sent on")
        self.assertEqual(lp.mqtt_line({}), "")
        self.assertEqual(lp.mqtt_line(None), "")

    def test_both_warnings_stand_one_per_line(self):
        both = lp.standing_lines(self.snap(True, True)).split("\n")
        self.assertEqual(len(both), 2)
        self.assertIn("variant", both[0])
        self.assertIn("MQTT", both[1])
        self.assertEqual(lp.standing_lines({}), "")

    def test_the_channel_list_carries_what_the_radio_says_about_each_channel(self):
        ch = types.SimpleNamespace(index=0, role=1, settings=types.SimpleNamespace(
            name="", psk=bytes([73]), uplink_enabled=True, downlink_enabled=False))
        got = lora.configured_channels([ch], "LongFast")
        self.assertEqual((got[0]["key"], got[0]["uplink"], got[0]["downlink"]), ("public_variant", True, False))
        bare = types.SimpleNamespace(index=1, role=2, settings=types.SimpleNamespace(name="Hill", psk=b"k" * 32))
        self.assertEqual((lora.configured_channels([bare])[0]["uplink"], lora.configured_channels([bare])[0]["downlink"]),
                         (False, False))



if __name__ == "__main__":
    unittest.main()
