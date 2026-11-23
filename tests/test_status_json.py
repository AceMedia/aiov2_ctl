"""
Tests for `aiov2_ctl --status --json`.

    python3 -m unittest discover -s tests
"""
import json
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402

SUMMARY = {"source": "AC", "status": "Charging", "capacity": 100, "direction": "idle",
           "mode": "AC", "voltage": 4.21, "current": 0.03, "power": 0.12}


class StatusDataTests(unittest.TestCase):
    def data(self, summary=SUMMARY, rails=True):
        with mock.patch.object(a.GpioController, "get_pin_state", return_value=(rails, "x")), \
                mock.patch.object(a.Telemetry, "power_summary", return_value=summary), \
                mock.patch.object(a.Telemetry, "last_discarded", (2, 8)), \
                mock.patch.object(a, "detect_companions", return_value=[]):
            return a.status_data()

    def test_shape_is_json_safe(self):
        d = self.data()
        json.dumps(d)
        self.assertEqual(set(d["rails"]), set(a.GPIO_MAP))
        self.assertEqual(d["rails"]["GPS"], "on")
        self.assertEqual(d["power"]["voltage"], 4.21)
        self.assertEqual(d["gauge_ignored"], {"ignored": 2, "taken": 8})
        self.assertFalse(d["gauge_warning"])

    def test_no_reading(self):
        d = self.data(summary=None, rails=False)
        self.assertIsNone(d["power"])
        self.assertFalse(d["gauge_warning"])
        self.assertEqual(d["rails"]["SDR"], "off")

    def test_gauge_disagreement_is_flagged(self):
        d = self.data(summary=dict(SUMMARY, voltage=2.7))
        self.assertTrue(d["gauge_warning"])

    def test_it_only_reads(self):
        import inspect
        src = inspect.getsource(a.status_data)
        for word in ("set_gpio", "write", "enable", "disable", "subprocess"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
