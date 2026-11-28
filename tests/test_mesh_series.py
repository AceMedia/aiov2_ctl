"""Each node's short history of battery level and signal-to-noise as heard here, for the small charts in the node
details. No radio and no Qt are needed."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_agent import lora  # noqa: E402
from ops_gui import lora_panel as lp, pixel  # noqa: E402
from tests.test_mesh_connection import Bus, Link, engine  # noqa: E402

NOW = 1_800_000_000


class AppendReadingTests(unittest.TestCase):
    def test_it_is_bounded_to_the_newest(self):
        series = []
        for i in range(100):
            series = lora.append_reading(series, NOW + i, i, limit=60)
        self.assertEqual(len(series), 60)
        self.assertEqual(series[0], (NOW + 40, 40.0))
        self.assertEqual(series[-1], (NOW + 99, 99.0))
        self.assertEqual(lora.SERIES_LIMIT, 60)

    def test_missing_and_unusable_readings_change_nothing(self):
        series = lora.append_reading([], NOW, 5)
        for bad in (None, "x", float("nan"), float("inf")):
            self.assertEqual(lora.append_reading(series, NOW + 1, bad), series, bad)
        self.assertEqual(lora.append_reading(series, None, 3), series)
        self.assertEqual(lora.append_reading(None, NOW, None), [])

    def test_out_of_order_goes_in_its_place_and_the_same_time_replaces(self):
        series = lora.append_reading([], NOW + 10, 1)
        series = lora.append_reading(series, NOW, 2)
        series = lora.append_reading(series, NOW + 5, 3)
        self.assertEqual([p[0] for p in series], [NOW, NOW + 5, NOW + 10])
        series = lora.append_reading(series, NOW + 5, 9)
        self.assertEqual(series, [(NOW, 2.0), (NOW + 5, 9.0), (NOW + 10, 1.0)])

    def test_an_old_reading_out_of_order_falls_off_when_full(self):
        series = [(NOW + i, float(i)) for i in range(3)]
        self.assertEqual(lora.append_reading(series, NOW - 1, 7, limit=3), series)


class CaptionTests(unittest.TestCase):
    def test_captions(self):
        self.assertEqual(lp.series_caption("battery", [], NOW), "Battery: no readings heard here yet")
        self.assertEqual(lp.series_caption("snr", [(NOW - 30, 6.25)], NOW), "SNR heard here, 1 reading: +6.2 dB, now")
        self.assertEqual(lp.series_caption("battery", [(NOW - 600, 80), (NOW - 120, 76)], NOW),
                         "Battery heard here, 2 readings: 80% to 76%, latest 2m ago")

    def test_values_feed_the_apps_sparkline_helper(self):
        values = lp.series_values([(NOW, 50.0), (NOW + 1, 100.0)])
        self.assertEqual(values, [50.0, 100.0])
        cols = pixel.spark_columns(values, *lp.SPARK_RANGES["battery"], 40, 26)
        self.assertEqual(len(cols), 2)
        self.assertLess(cols[1][1], cols[0][1])            # the higher level is drawn higher up


class EngineSeriesTests(unittest.TestCase):
    def test_signal_and_battery_heard_here_are_kept_and_external_power_is_not_a_level(self):
        bus, link = Bus(), Link()
        e = engine(bus, link)
        for i, level in enumerate((90, None, 101)):
            telemetry = {"deviceMetrics": {"batteryLevel": level}} if level is not None else {}
            bus.send("meshtastic.receive", interface=link, packet={
                "from": 0xBEEF, "fromId": "!0000beef", "id": 20 + i, "rxTime": NOW + i, "rxSnr": 5.0 + i,
                "decoded": {"portnum": "TELEMETRY_APP", "telemetry": telemetry}})
        series = e.snapshot()["node_series"]["!0000beef"]
        self.assertEqual(series["snr"], [(NOW, 5.0), (NOW + 1, 6.0), (NOW + 2, 7.0)])
        self.assertEqual(series["battery"], [(NOW, 90.0)])
        self.assertTrue(all(math.isfinite(v) for _, v in series["snr"]))


if __name__ == "__main__":
    unittest.main()
