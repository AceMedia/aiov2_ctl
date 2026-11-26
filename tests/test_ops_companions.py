import json
import subprocess
import unittest
from unittest import mock

from ops_agent import companions

FLIPPER = {"kind": "flipper", "label": "Flipper Zero", "name": "Flipper", "tty": "/dev/ttyACM0"}
ESP = {"kind": "esp32", "label": "ESP32", "name": "esp", "tty": "/dev/ttyACM1"}
GENERIC = {"kind": "possible-esp32", "label": "CP210x USB-serial", "name": "x", "tty": "/dev/ttyUSB0"}


def fake_out(data):
    return mock.patch("subprocess.check_output", return_value=json.dumps(data))


class GetTests(unittest.TestCase):
    def test_reads_json(self):
        with fake_out({"usb_rail": "on", "devices": [FLIPPER]}):
            self.assertEqual(companions.get_companions()["usb_rail"], "on")

    def test_every_failure_gives_empty(self):
        bad = [FileNotFoundError(), subprocess.CalledProcessError(2, "x"),
               subprocess.TimeoutExpired("x", 5)]
        for exc in bad:
            with mock.patch("subprocess.check_output", side_effect=exc):
                self.assertEqual(companions.get_companions(), {})

    def test_old_aiov2_ctl_text_output_gives_empty(self):
        with mock.patch("subprocess.check_output", return_value="Companion devices\n----"):
            self.assertEqual(companions.get_companions(), {})

    def test_wrong_shape_gives_empty(self):
        for odd in ([], {"devices": "x"}, {"usb_rail": "on"}):
            with fake_out(odd):
                self.assertEqual(companions.get_companions(), {})

    def test_call_is_bounded_and_read_only(self):
        with fake_out({"devices": []}) as m:
            companions.get_companions()
        args, kwargs = m.call_args
        self.assertEqual(args[0], ["aiov2_ctl", "--companions", "--json"])
        self.assertEqual(kwargs["timeout"], companions.TIMEOUT_SECONDS)


class PortTests(unittest.TestCase):
    def test_only_sure_companions_are_skipped(self):
        data = {"devices": [FLIPPER, ESP, GENERIC]}
        self.assertEqual(companions.companion_ports(data), {"/dev/ttyACM0", "/dev/ttyACM1"})

    def test_empty(self):
        self.assertEqual(companions.companion_ports({}), set())

    def test_summary(self):
        self.assertEqual(companions.summary({}), "")
        self.assertEqual(companions.summary({"devices": [FLIPPER, GENERIC]}),
                         "Companions: Flipper Zero @ /dev/ttyACM0, CP210x USB-serial (possible) @ /dev/ttyUSB0")


if __name__ == "__main__":
    unittest.main()
