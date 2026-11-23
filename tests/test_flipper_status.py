"""
Tests for --flipper status: gathering, module detection and suggestions.

    python3 -m unittest discover -s tests
"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402


class FakeCli:
    def __init__(self, replies):
        self.replies = replies
        self.sent = []

    def run(self, command):
        self.sent.append(command)
        return self.replies.get(command, "Unknown command")


REPLIES = {
    "device_info": "firmware_version : 1.4.3\nfirmware_origin_fork : Unleashed\n",
    "info power": "charge.level : 83\ncharge.state : discharging\nother : x\n",
    "storage list /ext": "[D] apps\n",
    "storage list /ext/apps": "[D] GPIO\n[F] readme.txt 1b\n",
    "storage list /ext/apps/GPIO": "[F] esp32_wifi_marauder.fap 90kb\n[F] notes.txt 1b\n",
}


class GatherTests(unittest.TestCase):
    def test_full_picture(self):
        s = a.gather_flipper_status(FakeCli(REPLIES), ["wifi-devboard"])
        self.assertEqual(s["sd_card"], "present")
        self.assertEqual(s["apps"], ["esp32_wifi_marauder"])
        self.assertEqual(s["battery"]["charge.level"], "83")
        self.assertNotIn("other", s["battery"])
        self.assertEqual(s["declared_modules"], ["wifi-devboard"])

    def test_missing_sd_card(self):
        r = dict(REPLIES, **{"storage list /ext": "Storage error: not ready"})
        s = a.gather_flipper_status(FakeCli(r))
        self.assertEqual(s["sd_card"], "missing")
        self.assertEqual(s["apps"], [])

    def test_only_reads_are_sent(self):
        cli = FakeCli(REPLIES)
        a.gather_flipper_status(cli)
        for c in cli.sent:
            self.assertTrue(c.startswith(("device_info", "info ", "power info", "storage list")), c)


class SuggestionTests(unittest.TestCase):
    def test_bridge_mode_with_board(self):
        t = a.flipper_suggestions({"mode": "bridge", "wifi_board": "present"})
        self.assertIn("--marauder scan", " ".join(t))

    def test_unchecked_board_says_how_to_check(self):
        t = a.flipper_suggestions({"mode": "cli", "sd_card": "present", "apps": [],
                                   "wifi_board": "unchecked", "declared_modules": []})
        self.assertIn("not checked", " ".join(t))

    def test_declared_board_without_app(self):
        t = a.flipper_suggestions({"mode": "cli", "sd_card": "present", "apps": [],
                                   "wifi_board": "unchecked",
                                   "declared_modules": ["wifi-devboard"]})
        self.assertIn("no Marauder app", " ".join(t))

    def test_no_dashes_in_text(self):
        t = a.flipper_suggestions({"mode": "bridge", "wifi_board": "present"})
        self.assertNotIn("—", " ".join(t))


class DeclaredTests(unittest.TestCase):
    def test_only_known_names_survive(self):
        self.assertEqual(a.flipper_declared_modules({"flipper_modules": ["nrf24", "bogus", 3]}), ["nrf24"])
        self.assertEqual(a.flipper_declared_modules({"flipper_modules": "nrf24"}), [])
        self.assertEqual(a.flipper_declared_modules({}), [])


class StatusCommandTests(unittest.TestCase):
    def test_bridge_mode_is_detected_when_cli_is_silent(self):
        out = io.StringIO()
        with mock.patch.object(a, "_flipper_port", return_value="/dev/x"), \
                mock.patch.object(a.FlipperCli, "open",
                                  side_effect=a.FlipperError("No CLI prompt from the Flipper.")), \
                mock.patch.object(a, "probe_wifi_board", return_value="present"), \
                mock.patch.object(a, "flipper_declared_modules", return_value=[]), \
                redirect_stdout(out):
            rc = a.flipper_command(["status", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["mode"], "bridge")
        self.assertEqual(data["wifi_board"], "present")

    def test_no_flipper_is_a_clear_error(self):
        out = io.StringIO()
        with mock.patch.object(a, "detect_companions", return_value=[]), redirect_stdout(out):
            self.assertEqual(a.flipper_command(["status"]), 1)
        self.assertIn("No Flipper found", out.getvalue())


if __name__ == "__main__":
    unittest.main()
