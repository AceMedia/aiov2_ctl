"""
Tests for companion device detection (Flipper Zero, ESP32 boards).

Uses a fake sysfs tree in a temp directory, so no hardware is needed:

    python3 -m unittest discover -s tests
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402


def make_device(base, name, vid, pid, product=None, serial=None, tty=None):
    dev = os.path.join(base, name)
    os.makedirs(dev)
    for fname, value in (("idVendor", vid), ("idProduct", pid),
                         ("product", product), ("serial", serial)):
        if value is not None:
            with open(os.path.join(dev, fname), "w") as f:
                f.write(value + "\n")
    if tty:
        os.makedirs(os.path.join(dev, f"{name}:1.0", "tty", tty))
    return dev


class DetectCompanionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        patcher = mock.patch.object(a, "USB_SYSFS_BASE", self.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_nothing_attached(self):
        make_device(self.base, "1-1", "0bda", "2832")  # the on-board SDR
        self.assertEqual(a.detect_companions(), [])
        self.assertEqual(a.companions_summary_line([]), "Companions: none")

    def test_flipper_detected_with_port(self):
        make_device(self.base, "1-2", "0483", "5740",
                    product="Flipper Aceflip", serial="flip_Aceflip", tty="ttyACM0")
        found = a.detect_companions()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "flipper")
        self.assertEqual(found[0]["tty"], "/dev/ttyACM0")
        self.assertEqual(found[0]["serial"], "flip_Aceflip")
        self.assertIn("Flipper Zero @ /dev/ttyACM0", a.companions_summary_line(found))

    def test_esp32_native_usb(self):
        make_device(self.base, "1-3", "303a", "0002", tty="ttyACM1")
        self.assertEqual(a.detect_companions()[0]["kind"], "esp32")

    def test_generic_serial_chip_is_only_possible(self):
        make_device(self.base, "1-4", "10c4", "ea60", tty="ttyUSB0")
        found = a.detect_companions()
        self.assertEqual(found[0]["kind"], "possible-esp32")
        self.assertIn("(possible)", a.companions_summary_line(found))

    def test_interfaces_are_ignored(self):
        # An interface directory must not be mistaken for a device.
        make_device(self.base, "1-2:1.0", "0483", "5740")
        self.assertEqual(a.detect_companions(), [])

    def test_device_without_serial_port(self):
        make_device(self.base, "1-2", "0483", "5740", product="Flipper")
        self.assertIsNone(a.detect_companions()[0]["tty"])

    def test_missing_sysfs_does_not_crash(self):
        with mock.patch.object(a, "USB_SYSFS_BASE", os.path.join(self.base, "nope")):
            self.assertEqual(a.detect_companions(), [])


class ShowCompanionsTests(unittest.TestCase):
    def test_hint_when_usb_rail_off(self):
        with mock.patch.object(a, "detect_companions", return_value=[]), \
             mock.patch.object(a.GpioController, "get_gpio", return_value=False):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(a.show_companions(), 0)
        self.assertIn("USB rail is OFF", out.getvalue())

    def test_permission_hint(self):
        dev = {"kind": "flipper", "name": "Flipper", "label": "Flipper Zero",
               "vid": "0483", "pid": "5740", "tty": "/dev/ttyACM0", "serial": None}
        with mock.patch.object(a, "detect_companions", return_value=[dev]), \
             mock.patch.object(a.os, "access", return_value=False):
            out = io.StringIO()
            with redirect_stdout(out):
                a.show_companions()
        self.assertIn("no permission", out.getvalue())


class UdevRuleTests(unittest.TestCase):
    def test_rule_uses_uaccess_not_world_writable(self):
        rules = a.COMPANION_UDEV_RULES
        self.assertIn('TAG+="uaccess"', rules)
        self.assertNotIn("MODE", rules)
        self.assertIn('ENV{ID_MM_DEVICE_IGNORE}="1"', rules)
        self.assertIn('ATTRS{idVendor}=="0483"', rules)


if __name__ == "__main__":
    unittest.main()
