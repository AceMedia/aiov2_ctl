"""Tests for the pure Flipper session helpers (no Flipper, no Qt)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_gui import flipper_session as s  # noqa: E402

# Written by hand in the layout of the stock firmware's `device_info` answer.
STOCK = """hardware_model         : Flipper Zero
hardware_name          : Aceflip
hardware_ver           : 12
hardware_region        : 1
firmware_version       : 1.1.2
firmware_branch        : release
firmware_origin_fork   : Official
firmware_origin_git    : https://github.com/flipperdevices/flipperzero-firmware
firmware_commit        : 4b3b2a1f
firmware_build_date    : 21-10-2026
radio_alive            : true
radio_stack_major      : 1
radio_stack_minor      : 17
radio_stack_type       : 3
enclave_valid          : true
protobuf_version_major : 0
protobuf_version_minor : 24
"""

# A custom fork, written in the dotted layout newer firmware prints for `info device`.
FORK = """hardware.model         : Flipper Zero
hardware.name          : Ghosty
hardware.ver           : 12
firmware.version       : mntm-008
firmware.branch        : mntm-dev
firmware.origin.fork   : Momentum
firmware.origin.git    : https://github.com/Next-Flip/Momentum-Firmware
radio.alive            : false
radio.stack.major      : 1
radio.stack.minor      : 19
"""


class DeviceInfoTests(unittest.TestCase):
    def test_stock(self):
        r = s.parse_device_info(STOCK)
        self.assertEqual(r["model"], "Flipper Zero")
        self.assertEqual(r["name"], "Aceflip")
        self.assertEqual(r["firmware_version"], "1.1.2")
        self.assertEqual(r["branch"], "release")
        self.assertEqual(r["origin"], "Official")
        self.assertEqual(r["origin_git"], "https://github.com/flipperdevices/flipperzero-firmware")
        self.assertEqual(r["radio_stack"], "1.17 (type 3)")
        self.assertEqual(r["rpc_version"], "0.24")
        self.assertIn(("Bluetooth radio core", "running"), r["modules"])
        self.assertIn(("secure enclave", "valid"), r["modules"])

    def test_fork_is_what_the_device_says(self):
        r = s.parse_device_info(FORK)
        self.assertEqual(r["origin"], "Momentum")
        self.assertEqual(r["branch"], "mntm-dev")
        self.assertEqual(r["firmware_version"], "mntm-008")
        self.assertEqual(r["radio_stack"], "1.19")
        self.assertEqual(r["rpc_version"], "")
        self.assertEqual(r["modules"], [("Bluetooth radio core", "not running")])

    def test_origin_is_not_guessed_from_the_version(self):
        r = s.parse_device_info("firmware_version : unlshd-095\n")
        self.assertEqual(r["origin"], "")
        self.assertIn(("Origin", "not reported"), s.about_rows(r))

    def test_dict_from_status(self):
        r = s.parse_device_info({"hardware_name": "Aceflip", "firmware_version": "1.4.3", 5: "x", "a": None})
        self.assertEqual((r["name"], r["firmware_version"]), ("Aceflip", "1.4.3"))

    def test_empty_and_garbled(self):
        for bad in ("", None, 42, [], {}, "no colons here\n\x00\x01", "Usage: device_info\n",
                    ":::\n : \n", "random : words\nmore : stuff"):
            self.assertEqual(s.parse_device_info(bad), {}, repr(bad))

    def test_control_characters_dropped(self):
        r = s.parse_device_info("hardware_name : Ace\x1b[31mflip\n")
        self.assertEqual(r["name"], "Ace[31mflip")

    def test_about_rows(self):
        rows = dict(s.about_rows(s.parse_device_info(STOCK)))
        self.assertEqual(rows["Hardware"], "Flipper Zero “Aceflip” hardware 12")
        self.assertEqual(rows["Firmware"], "1.1.2, built 21-10-2026")
        self.assertTrue(rows["Origin"].startswith("Official ("))
        self.assertIn("secure enclave valid", rows["Reports"])
        self.assertEqual(s.about_rows({}), [("Details", "The Flipper has not described itself yet.")])


if __name__ == "__main__":
    unittest.main()

