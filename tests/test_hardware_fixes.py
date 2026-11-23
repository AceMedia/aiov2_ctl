"""
Tests for fixes found on the real uConsole with the revised AIO board:
the noisy battery gauge and the installer's sudoers rule.

    python3 -m unittest discover -s tests
"""
import inspect
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402


def gauge(samples):
    """Make _read_int return voltage/current pairs (in microvolts/microamps) in turn."""
    seq = []
    for v, i in samples:
        seq += [v, i]
    it = iter(seq)
    return mock.patch.object(a.Telemetry, "_read_int", side_effect=lambda p: next(it, None))


@mock.patch.object(a.time, "sleep", lambda s: None)
class BatteryGaugeTests(unittest.TestCase):
    def test_steady_reading_is_unchanged(self):
        with gauge([(4228000, 150000)] * 5):
            r = a.Telemetry.battery_v_i_w()
        self.assertEqual(r["voltage"], 4.23)
        self.assertEqual(r["current"], 0.15)
        self.assertEqual(a.Telemetry.last_discarded, (0, 5))

    def test_glitches_seen_on_the_revised_board_are_ignored(self):
        # Captured from the uConsole: roughly half the samples read 1.3 to 1.7 V at 0 A.
        with gauge([(1656000, 0), (4229000, 166000), (1464000, 0),
                    (4228000, 162000), (1302000, 0)]):
            r = a.Telemetry.battery_v_i_w()
        self.assertEqual(r["voltage"], 4.23)
        self.assertGreater(r["current"], 0.1)
        self.assertEqual(a.Telemetry.last_discarded, (3, 5))

    def test_ramp_values_inside_the_cell_range_are_ignored_too(self):
        # Real capture: the glitch ramps through 3.4 V and 2.6 V as well as 1.5 V.
        with gauge([(2620000, 0), (4229000, 166000), (3424000, 96000), (1500000, 0),
                    (4228000, 162000), (2900000, 0), (4230000, 150000), (1650000, 0)]):
            r = a.Telemetry.battery_v_i_w()
        self.assertEqual(r["voltage"], 4.23)
        self.assertEqual(a.Telemetry.last_discarded, (5, 8))

    def test_all_implausible_gives_nothing_rather_than_a_wrong_number(self):
        with gauge([(1500000, 0)] * 5):
            self.assertIsNone(a.Telemetry.battery_v_i_w())
        self.assertEqual(a.Telemetry.last_discarded, (5, 5))

    def test_unreadable_gauge(self):
        with mock.patch.object(a.Telemetry, "_read_int", return_value=None):
            self.assertIsNone(a.Telemetry.battery_v_i_w())
        self.assertEqual(a.Telemetry.last_discarded, (0, 0))

    def test_discharging_current_keeps_its_sign(self):
        with gauge([(3800000, -400000)] * 5):
            r = a.Telemetry.battery_v_i_w()
        self.assertEqual(r["current"], -0.4)
        self.assertLess(r["power"], 0)

    def test_status_mentions_ignored_readings(self):
        summary = {"source": "AC", "status": "Charging", "capacity": 100,
                   "direction": "charging", "mode": "AC", "voltage": 4.23,
                   "current": 0.15, "power": 0.63}
        out = io.StringIO()
        with mock.patch.object(a.Telemetry, "power_summary", return_value=summary), \
                mock.patch.object(a, "detect_companions", return_value=[]), \
                mock.patch.object(a.Telemetry, "last_discarded", (3, 5)), \
                mock.patch.object(a.GpioController, "get_gpio", return_value=0), \
                redirect_stdout(out):
            a.show_status()
        self.assertIn("3 of 5 readings ignored", out.getvalue())


class InstallSudoersTests(unittest.TestCase):
    def test_installer_no_longer_writes_the_broad_rule(self):
        src = inspect.getsource(a.install_self)
        self.assertNotIn("ALL ALL=", src)
        self.assertIn("setup_sudoers()", src)

    def test_generated_rules_are_for_one_user_with_exact_commands(self):
        text = a.build_sudoers("shane")
        rule = [l for l in text.splitlines() if l.startswith("shane ")]
        self.assertEqual(len(rule), 1)
        self.assertNotIn("*", text)
        self.assertNotIn("ALL ALL", text)
        self.assertIn("--boot-rail USB on", text)


if __name__ == "__main__":
    unittest.main()
