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


class CapabilityTests(unittest.TestCase):
    def test_stock_with_card_offers_everything(self):
        caps = s.capabilities(s.parse_device_info(STOCK), {"mode": "cli", "sd_card": "present"})
        self.assertEqual(set(caps), set(s.FEATURES))
        self.assertTrue(all(ok for ok, _ in caps.values()))
        self.assertEqual(s.unavailable_rows(caps), [])

    def test_no_rpc_version_greys_screen_and_transfers_only(self):
        caps = s.capabilities(s.parse_device_info(FORK), {"mode": "cli", "sd_card": "present"})
        for key in ("live_screen", "copy", "send"):
            self.assertFalse(caps[key][0], key)
            self.assertIn("RPC", caps[key][1])
        for key in ("files", "apps", "pins"):
            self.assertTrue(caps[key][0], key)

    def test_missing_card(self):
        caps = s.capabilities(s.parse_device_info(STOCK), {"mode": "cli", "sd_card": "missing"})
        self.assertFalse(caps["files"][0])
        self.assertEqual(caps["files"][1], "The Flipper reports no SD card.")
        self.assertTrue(caps["live_screen"][0])

    def test_bridge_mode_turns_all_off(self):
        caps = s.capabilities({}, {"mode": "bridge"})
        self.assertFalse(any(ok for ok, _ in caps.values()))
        self.assertEqual(len(s.unavailable_rows(caps)), len(s.FEATURES))

    def test_unknown_layout_takes_nothing_away(self):
        caps = s.capabilities({}, {"mode": "cli"})
        self.assertTrue(all(ok for ok, _ in caps.values()))
        self.assertTrue(all(ok for ok, _ in s.capabilities(None).values()))

    def test_available(self):
        caps = {"copy": (False, "x")}
        self.assertFalse(s.available(caps, "copy"))
        self.assertTrue(s.available(caps, "files"))
        self.assertTrue(s.available(None, "copy"))
        self.assertEqual(s.unavailable_rows(caps), [("Copy to uConsole", "x")])


class ClockTests(unittest.TestCase):
    T = s._dt.datetime

    def test_parse_reading(self):
        out = "Flipper clock: 2026-11-28 14:03:00 6\nThis computer: 2026-11-28 14:06:12\n"
        fl, pc = s.clock_reading(out)
        self.assertEqual(fl, self.T(2026, 11, 28, 14, 3, 0))
        self.assertEqual(pc, self.T(2026, 11, 28, 14, 6, 12))
        self.assertEqual(s.clock_text(s.clock_offset(fl, pc)), "Flipper clock is 3 min 12 s behind this computer.")

    def test_garbled_reading(self):
        for bad in ("", None, "Error: Timed out", "Flipper clock: 2026-13-45 99:00:00\nThis computer: x",
                    "Flipper clock: (no answer)\nThis computer: 2026-11-28 14:06:12"):
            fl, pc = s.clock_reading(bad)
            self.assertIsNone(s.clock_offset(fl, pc), repr(bad))
        self.assertEqual(s.clock_text(None), "Could not read the Flipper's clock.")

    def test_ahead_and_matching(self):
        base = self.T(2026, 1, 1, 0, 0, 0)
        self.assertEqual(s.clock_offset(base + s._dt.timedelta(hours=2, minutes=5), base), 7500)
        self.assertEqual(s.clock_text(7500), "Flipper clock is 2 h 5 min ahead of this computer.")
        self.assertIn("matches", s.clock_text(2))
        self.assertIn("matches", s.clock_text(-1))
        self.assertIn("3 s behind", s.clock_text(-3))

    def test_durations(self):
        self.assertEqual(s.duration_text(0), "0 s")
        self.assertEqual(s.duration_text(-192), "3 min 12 s")
        self.assertEqual(s.duration_text(3600), "1 h")
        self.assertEqual(s.duration_text(86400 + 3600), "1 day 1 h")
        self.assertEqual(s.duration_text(3 * 86400), "3 days")

    def test_args(self):
        self.assertEqual(s.clock_args(), ["aiov2_ctl", "--flipper", "clock"])
        self.assertEqual(s.clock_set_args()[-2:], ["set", "--yes"])

    def test_clock_is_a_cli_feature(self):
        self.assertTrue(s.capabilities(s.parse_device_info(FORK), {"mode": "cli"})["clock"][0])
        self.assertFalse(s.capabilities({}, {"mode": "bridge"})["clock"][0])


class DateCommandTests(unittest.TestCase):
    """The one clock-setting command aiov2_ctl sends."""

    def test_text(self):
        import aiov2_ctl as a
        when = s._dt.datetime(2026, 11, 28, 9, 4, 5)        # a Saturday
        self.assertEqual(a.flipper_date_command(when), "date 2026-11-28 09:04:05 6")
        self.assertTrue(a.flipper_date_command(s._dt.datetime(2026, 11, 30, 23, 59, 59)).endswith(" 1"))
        self.assertRegex(a.flipper_date_command(when), r"^date [0-9: -]+$")   # built here, never from input

    def test_refused_answers(self):
        import aiov2_ctl as a
        self.assertTrue(a.flipper_date_refused("Invalid datetime format"))
        self.assertTrue(a.flipper_date_refused("Usage:\r\ndate [YYYY-MM-DD HH:MM:SS d]"))
        self.assertFalse(a.flipper_date_refused(""))

    def test_clock_command_with_a_stand_in(self):
        import io
        from contextlib import redirect_stdout
        import aiov2_ctl as a

        class Cli:
            def __init__(self):
                self.sent = []

            def run(self, command):
                self.sent.append(command)
                return "2026-11-28 14:03:00 6" if command == "date" else ""

        cli = Cli()
        with redirect_stdout(io.StringIO()) as out:
            a.flipper_clock(cli, [])
        self.assertEqual(cli.sent, ["date"])                       # reading never sets
        fl, pc = s.clock_reading(out.getvalue())
        self.assertEqual(fl, s._dt.datetime(2026, 11, 28, 14, 3, 0))
        self.assertIsNotNone(pc)
        with self.assertRaises(a.FlipperError):
            a.flipper_clock(cli, ["set"])                          # no --yes, nothing sent
        self.assertEqual(cli.sent, ["date"])
        with redirect_stdout(io.StringIO()):
            a.flipper_clock(cli, ["set"], yes=True)
        self.assertEqual(len(cli.sent), 3)
        self.assertTrue(cli.sent[1].startswith("date 2"))
        self.assertEqual(cli.sent[2], "date")                      # read back to confirm
        with self.assertRaises(a.FlipperError):
            a.flipper_clock(cli, ["bogus"], yes=True)


if __name__ == "__main__":
    unittest.main()

