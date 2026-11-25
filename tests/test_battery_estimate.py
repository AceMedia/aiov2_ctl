import unittest

import aiov2_ctl as a


class VoltageEstimateTests(unittest.TestCase):
    """About how full the cell is, from its voltage."""

    def test_a_full_and_an_empty_cell(self):
        self.assertEqual(a.percent_from_voltage(4.20), 100)
        self.assertEqual(a.percent_from_voltage(4.35), 100)
        self.assertEqual(a.percent_from_voltage(3.00), 0)
        self.assertEqual(a.percent_from_voltage(2.80), 0)

    def test_it_rises_with_voltage_all_the_way(self):
        got = [a.percent_from_voltage(v / 100) for v in range(300, 421)]
        self.assertEqual(got, sorted(got))

    def test_a_cell_under_load_sags_and_that_is_allowed_for(self):
        resting = a.percent_from_voltage(3.65)
        loaded = a.percent_from_voltage(3.65, -2.7)          # the walk: 3.65 V while drawing 2.7 A
        self.assertGreater(loaded, resting)
        self.assertTrue(40 <= loaded <= 65, loaded)

    def test_a_cell_being_charged_reads_high_and_that_is_allowed_for(self):
        self.assertLess(a.percent_from_voltage(4.10, 1.5), a.percent_from_voltage(4.10))

    def test_no_reading_gives_no_estimate(self):
        self.assertIsNone(a.percent_from_voltage(None))
        self.assertIsNone(a.percent_from_voltage(0))


class StuckGaugeTests(unittest.TestCase):
    def test_a_gauge_saying_full_at_half_charge_is_not_believed(self):
        self.assertTrue(a.gauge_unbelievable(100, a.percent_from_voltage(3.65, -2.7)))

    def test_a_gauge_roughly_in_step_with_the_voltage_is_believed(self):
        self.assertFalse(a.gauge_unbelievable(62, 50))
        self.assertFalse(a.gauge_unbelievable(100, 100))
        self.assertFalse(a.gauge_unbelievable(100, 85))

    def test_with_either_missing_the_gauge_stands(self):
        self.assertFalse(a.gauge_unbelievable(None, 50))
        self.assertFalse(a.gauge_unbelievable(100, None))

    def summary(self, cap, volts, amps, design=24790000):
        files = {"capacity": cap, "energy_full_design": design}
        real_int, real_viw = a.Telemetry._read_int, a.Telemetry.battery_v_i_w
        real_ac, real_status = a.Telemetry.ac_online, a.Telemetry.battery_status
        a.Telemetry._read_int = staticmethod(lambda path: files.get(path.rsplit("/", 1)[-1]))
        a.Telemetry.battery_v_i_w = staticmethod(lambda: {"voltage": volts, "current": amps, "power": volts * amps})
        a.Telemetry.ac_online = staticmethod(lambda: False)
        a.Telemetry.battery_status = staticmethod(lambda: "Discharging")
        try:
            return a.Telemetry.power_summary()
        finally:
            a.Telemetry._read_int, a.Telemetry.battery_v_i_w = real_int, real_viw
            a.Telemetry.ac_online, a.Telemetry.battery_status = real_ac, real_status

    def test_the_status_gives_the_estimate_and_says_where_it_came_from(self):
        s = self.summary(100, 3.65, -2.7)
        self.assertEqual(s["capacity_source"], "voltage")
        self.assertEqual(s["capacity_gauge"], 100)
        self.assertTrue(40 <= s["capacity"] <= 65)
        self.assertEqual(s["energy_full_wh"], 24.79)
        self.assertTrue(a.gauge_disagrees(s))

    def test_a_working_gauge_is_passed_on_untouched(self):
        s = self.summary(55, 3.80, -1.0)
        self.assertEqual((s["capacity"], s["capacity_source"], s["capacity_gauge"]), (55, "gauge", 55))
        self.assertFalse(a.gauge_disagrees(s))


if __name__ == "__main__":
    unittest.main()
