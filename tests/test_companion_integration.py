"""
Tests for the phase 4 companion integration: JSON output, USB rail hints,
stable device names and the default captures folder.

    python3 -m unittest discover -s tests
"""
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aiov2_ctl as a  # noqa: E402
from test_flipper_rpc import FakeRpcFlipper  # noqa: E402

FLIPPER = {"kind": "flipper", "name": "Flipper", "label": "Flipper Zero",
           "vid": "0483", "pid": "5740", "tty": "/dev/ttyACM0", "serial": "flip_x"}


class RailTests(unittest.TestCase):
    def test_states(self):
        for level, want in ((1, "on"), (0, "off"), (None, None)):
            with mock.patch.object(a.GpioController, "get_gpio", return_value=level):
                self.assertEqual(a.usb_rail_state(), want)

    def test_unreadable_rail_is_not_an_error(self):
        with mock.patch.object(a.GpioController, "get_gpio", side_effect=OSError):
            self.assertIsNone(a.usb_rail_state())
            self.assertEqual(a.usb_rail_hint(), "")

    def test_hint_only_when_off(self):
        with mock.patch.object(a.GpioController, "get_gpio", return_value=0):
            self.assertIn("USB on", a.usb_rail_hint())
        with mock.patch.object(a.GpioController, "get_gpio", return_value=1):
            self.assertEqual(a.usb_rail_hint(), "")

    def test_missing_device_errors_mention_a_dead_rail(self):
        with mock.patch.object(a, "detect_companions", return_value=[]), \
                mock.patch.object(a.GpioController, "get_gpio", return_value=0):
            with self.assertRaises(a.FlipperError) as cm:
                a._flipper_port()
            self.assertIn("rail is OFF", str(cm.exception))
            with self.assertRaises(a.MarauderError) as cm:
                a._marauder_port()
            self.assertIn("rail is OFF", str(cm.exception))


class JsonTests(unittest.TestCase):
    def test_json_output(self):
        out = io.StringIO()
        with mock.patch.object(a, "detect_companions", return_value=[FLIPPER]), \
                mock.patch.object(a.GpioController, "get_gpio", return_value=1), \
                redirect_stdout(out):
            self.assertEqual(a.show_companions(as_json=True), 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["usb_rail"], "on")
        self.assertEqual(data["devices"][0]["tty"], "/dev/ttyACM0")

    def test_json_with_nothing_attached(self):
        out = io.StringIO()
        with mock.patch.object(a, "detect_companions", return_value=[]), \
                mock.patch.object(a.GpioController, "get_gpio", return_value=None), \
                redirect_stdout(out):
            a.show_companions(as_json=True)
        data = json.loads(out.getvalue())
        self.assertEqual(data, {"usb_rail": None, "devices": []})


class StatusLineTests(unittest.TestCase):
    def test_companion_line_cannot_be_mistaken_for_a_rail(self):
        # ops.py reads --status lines that start with these names.
        line = a.companions_summary_line([FLIPPER])
        for name in ("GPS", "SDR", "LORA", "USB"):
            self.assertFalse(line.startswith(name))


class UdevTests(unittest.TestCase):
    def test_rules_give_stable_names(self):
        self.assertIn('SYMLINK+="aiov2-flipper"', a.COMPANION_UDEV_RULES)
        self.assertIn('SYMLINK+="aiov2-esp32"', a.COMPANION_UDEV_RULES)
        self.assertIn("uaccess", a.COMPANION_UDEV_RULES)


@unittest.skipUnless(hasattr(os, "openpty"), "needs a Unix pty")
class DefaultCaptureTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRpcFlipper()
        self.addCleanup(self.fake.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(a, "CAPTURES_DIR", os.path.join(self.tmp.name, "caps"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = a.flipper_command(["get", *args, "--port", self.fake.path])
        return rc, out.getvalue()

    def test_get_without_destination_uses_private_captures_folder(self):
        self.fake.files["/ext/notes.txt"] = b"hello"
        rc, out = self.get("/ext/notes.txt")
        self.assertEqual(rc, 0, out)
        dest = os.path.join(a.CAPTURES_DIR, "flipper", "notes.txt")
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"hello")
        self.assertEqual(stat.S_IMODE(os.stat(dest).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(dest)).st_mode), 0o700)

    def test_second_get_does_not_overwrite(self):
        self.fake.files["/ext/n"] = b"one"
        self.assertEqual(self.get("/ext/n")[0], 0)
        self.fake.files["/ext/n"] = b"two"
        rc, out = self.get("/ext/n")
        self.assertEqual(rc, 1)
        with open(os.path.join(a.CAPTURES_DIR, "flipper", "n"), "rb") as f:
            self.assertEqual(f.read(), b"one")

    def test_marauder_logs_share_the_same_folder(self):
        self.assertEqual(a.MARAUDER_LOG_DIR, os.path.expanduser("~/aiov2-captures"))


if __name__ == "__main__":
    unittest.main()
