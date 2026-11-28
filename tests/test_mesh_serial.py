"""The serial fallback opens only a port whose USB identity is a chip Meshtastic boards use, always by name, and says
which port it chose and why. A fake identity reader and a fake library stand in for the hardware."""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_agent import lora  # noqa: E402

IDS = {"/dev/ttyACM0": ("0483", "5740"),          # an STM32 board: not a Meshtastic chip
       "/dev/ttyUSB0": ("10c4", "ea60"),          # CP210x: a Heltec or T-Beam style board
       "/dev/ttyACM1": ("303a", "1001")}          # ESP32-S3 native USB


class ChoiceTests(unittest.TestCase):
    def test_the_first_port_on_the_allow_list_is_chosen_and_the_reason_given(self):
        port, why = lora.choose_serial_port(["/dev/ttyACM0", "/dev/ttyUSB0", "/dev/ttyACM1"], IDS.get)
        self.assertEqual(port, "/dev/ttyUSB0")
        self.assertEqual(why, "ttyUSB0 is a Silicon Labs CP210x (10c4:ea60)")

    def test_no_port_qualifies_and_it_says_why_for_each(self):
        port, why = lora.choose_serial_port(["/dev/ttyACM0", "/dev/ttyUSB1"], IDS.get)
        self.assertIsNone(port)
        self.assertIn("ttyACM0 (0483:5740) is not a chip Meshtastic boards use", why)
        self.assertIn("ttyUSB1: USB identity unknown", why)
        self.assertEqual(lora.choose_serial_port([], IDS.get), (None, "No USB serial devices found"))

    def test_a_reader_that_fails_counts_as_unknown(self):
        def broken(_):
            raise OSError("gone")
        self.assertIsNone(lora.choose_serial_port(["/dev/ttyACM0"], broken)[0])

    def test_the_allow_list(self):
        for ident in (("303a", "1001"), ("10C4", "EA60"), ("1a86", "7523"), ("1a86", "55d4"), ("239a", "8029"),
                      ("2e8a", "000a")):
            self.assertIsNotNone(lora.usb_chip(ident), ident)
        for ident in (("10c4", "ea70"), ("0403", "6001"), ("0483", "5740"), None):
            self.assertIsNone(lora.usb_chip(ident), ident)

    def test_sysfs_is_read_from_the_usb_device_above_the_tty(self):
        with tempfile.TemporaryDirectory() as root:
            usb = os.path.join(root, "1-1")
            iface = os.path.join(usb, "1-1:1.0")
            os.makedirs(iface)
            for name, value in (("idVendor", "303a\n"), ("idProduct", "1001\n")):
                with open(os.path.join(usb, name), "w") as f:
                    f.write(value)
            with mock.patch.object(lora.os.path, "realpath", return_value=iface):
                self.assertEqual(lora._sysfs_usb_identity("/dev/ttyACM0"), ("303a", "1001"))
            with mock.patch.object(lora.os.path, "realpath", return_value=os.path.join(root, "nothing")):
                self.assertIsNone(lora._sysfs_usb_identity("/dev/ttyACM9"))


class ConnectTests(unittest.TestCase):
    def connect(self, present, ids):
        opened = []

        def serial_interface(**kwargs):
            opened.append(kwargs)
            return types.SimpleNamespace(nodes={}, close=lambda: None)

        fake = types.SimpleNamespace(serial_interface=types.SimpleNamespace(SerialInterface=serial_interface))
        e = lora.LoRa()
        with mock.patch.object(lora.bootstrap, "MESHTASTIC_AVAILABLE", True), \
                mock.patch.object(lora.bootstrap, "MESHTASTIC_HOST", None), \
                mock.patch.object(lora.bootstrap, "meshtastic", fake, create=True), \
                mock.patch.object(lora.time, "sleep"), \
                mock.patch.object(lora.os.path, "exists", side_effect=lambda p: p in present), \
                mock.patch.object(lora.companions, "get_companions", return_value=[]), \
                mock.patch.object(lora.companions, "companion_ports", return_value=set()), \
                mock.patch.object(lora, "usb_identity", side_effect=lambda d: ids.get(d)), \
                mock.patch.object(lora, "_pubsub", return_value=None), \
                mock.patch.object(e, "_detect_serial_conflicts", return_value=[]):
            e.connect()
        return e, opened

    def test_it_opens_the_qualifying_port_by_name_and_says_so(self):
        e, opened = self.connect({"/dev/ttyACM0", "/dev/ttyUSB0"}, IDS)
        self.assertEqual(opened, [{"devPath": "/dev/ttyUSB0"}], "never auto-detection, never the STM32 board")
        self.assertTrue(e.active)
        self.assertIn("ttyUSB0 is a Silicon Labs CP210x", e.connection_status)

    def test_with_no_qualifying_port_nothing_is_opened(self):
        e, opened = self.connect({"/dev/ttyACM0"}, IDS)
        self.assertEqual(opened, [])
        self.assertFalse(e.active)
        self.assertFalse(e.connecting)
        self.assertIn("No Meshtastic radio on USB serial", e.connection_status)


if __name__ == "__main__":
    unittest.main()
