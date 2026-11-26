import unittest

from ops_gui import statusbar as s


class CpuTests(unittest.TestCase):
    def test_parse_and_percent(self):
        a = s.parse_cpu("cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 1 1 1")
        b = s.parse_cpu("cpu  200 0 200 750 150 0 0 0 0 0")
        self.assertEqual(a, (200, 1000))
        self.assertEqual(b, (400, 1300))
        self.assertAlmostEqual(s.cpu_percent(a, b), 66.67, places=1)

    def test_bad_input(self):
        for bad in ("", None, "cpu  a b c", "mem 1 2"):
            self.assertIsNone(s.parse_cpu(bad))
        self.assertIsNone(s.cpu_percent(None, (1, 2)))
        self.assertIsNone(s.cpu_percent((5, 10), (5, 10)))           # no time passed

    def test_percent_is_clamped(self):
        self.assertEqual(s.cpu_percent((0, 0), (10, 10)), 100.0)
        self.assertEqual(s.cpu_percent((10, 10), (10, 20)), 0.0)


class MemTests(unittest.TestCase):
    def test_mem(self):
        used, total, pct = s.parse_mem("MemTotal: 16777216 kB\nMemAvailable: 12582912 kB\n")
        self.assertEqual((used, total, pct), (4.0, 16.0, 25.0))
        self.assertIsNone(s.parse_mem(""))
        self.assertIsNone(s.parse_mem("MemTotal: x kB"))


class TextTests(unittest.TestCase):
    def test_uptime(self):
        self.assertEqual(s.uptime_text(90), "1m")
        self.assertEqual(s.uptime_text(3725), "1h 02m")
        self.assertEqual(s.uptime_text(90000), "1d 1h")

    def test_throttle(self):
        self.assertEqual(s.throttle_text(0), "")
        self.assertEqual(s.throttle_text(0x5), "under voltage, throttled")
        self.assertEqual(s.parse_throttled("throttled=0x50005"), 0x50005)
        self.assertEqual(s.parse_throttled("junk"), 0)
        self.assertEqual(s.parse_throttled(""), 0)

    def test_levels(self):
        self.assertEqual(s.level(None, 1, 2), "ok")
        self.assertEqual(s.level(0, 70, 90), "good")
        self.assertEqual(s.level(70, 70, 90), "warn")
        self.assertEqual(s.level(95, 70, 90), "hot")


class ChipTests(unittest.TestCase):
    STATS = {"cpu": 12.4, "temp": 52.0, "mem": (2.5, 15.8, 16.0), "load": 0.31, "disk": 20.0,
             "uptime": 4000.0, "ip": "192.168.0.83", "throttled": 0}

    def test_chips(self):
        c = dict((t.split()[0], lv) for t, lv in s.chips(self.STATS))
        self.assertEqual(c["CPU"], "good")
        self.assertEqual(c["RAM"], "good")
        self.assertIn("52°C", [t for t, _ in s.chips(self.STATS)])
        self.assertIn("RAM 2.5/15.8 GB", [t for t, _ in s.chips(self.STATS)])
        self.assertEqual(s.chips({}), [])

    def test_warnings(self):
        hot = dict(self.STATS, cpu=95, temp=82.0, mem=(15, 16, 94.0), throttled=0x4)
        levels = {t.split()[0]: lv for t, lv in s.chips(hot)}
        self.assertEqual((levels["CPU"], levels["RAM"], levels["power:"]), ("hot", "hot", "hot"))

    def test_html_is_escaped_and_ordered(self):
        out = s.chips_html([("a<b", "warn")], extra=[("Battery 100%", "good")])
        self.assertLess(out.index("Battery"), out.index("a&lt;b"))
        self.assertNotIn("a<b", out)

    def test_it_reads_this_machine(self):
        stats = s.read_stats()
        self.assertIsInstance(stats, dict)
        self.assertIn("mem", stats)

    def test_the_temperature_colour_follows_the_theme(self):
        from ops_gui import pixel
        stats = dict(self.STATS)
        dark = dict((k, lv) for k, _, lv in s.chip_items(stats, dark=True))["temp"]
        light = dict((k, lv) for k, _, lv in s.chip_items(stats, dark=False))["temp"]
        self.assertEqual(dark, pixel.temp_colour(stats["temp"], True))
        self.assertEqual(light, pixel.temp_colour(stats["temp"], False))


if __name__ == "__main__":
    unittest.main()
