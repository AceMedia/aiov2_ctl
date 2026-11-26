"""Pure helpers behind the SDR tab: readout, click mapping, waterfall colours, ranges."""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from ops_gui import sdr_panel as sp  # noqa: E402


class SdrPanelTests(unittest.TestCase):
    def test_frequency_readout(self):
        self.assertEqual(sp.format_freq(98.0), "98.000 000")
        self.assertEqual(sp.format_freq(27.60125), "27.601 250")
        self.assertEqual(sp.format_freq(1090.0), "1090.000 000")
        self.assertIn("---", sp.format_freq(None))

    def test_click_mapping_round_trips(self):
        for mhz in (97.2, 98.0, 99.1):
            f = sp.mhz_to_x(mhz, 98.0, 2_400_000)
            self.assertAlmostEqual(sp.x_to_mhz(f, 98.0, 2_400_000), mhz, places=4)
        self.assertEqual(sp.x_to_mhz(0.5, 98.0, 2_400_000), 98.0)
        self.assertEqual(sp.x_to_mhz(-3, 98.0, 2_400_000), 96.8)
        self.assertEqual(sp.x_to_mhz(9, 98.0, 2_400_000), 99.2)

    def test_downsample_and_waterfall_shape(self):
        spec = [float(i % 50) for i in range(2048)]
        self.assertEqual(len(sp.downsample(spec, 600)), 600)
        self.assertEqual(len(sp.downsample(spec[:100], 600)), 100)
        row = sp.waterfall_row(spec, 600, 0.0, 50.0)
        self.assertEqual(row.shape, (600, 3))
        self.assertEqual(sp.downsample([], 10).size, 0)

    def test_colour_map_runs_from_dark_to_hot(self):
        self.assertLess(int(sp.LUT[0].sum()), int(sp.LUT[128].sum()))
        self.assertGreater(int(sp.LUT[255][0]), 200)

    def test_display_range_is_steady_and_sane(self):
        lo, hi = sp.display_range([10.0] * 90 + [40.0] * 10)
        self.assertGreaterEqual(hi - lo, 20)
        lo2, hi2 = sp.display_range([10.0] * 90 + [40.0] * 10, (lo, hi))
        self.assertAlmostEqual(lo, lo2, delta=1.0)
        self.assertEqual(sp.display_range([], (1, 2)), (1, 2))

    def test_snr_fraction(self):
        self.assertEqual(sp.snr_fraction(None), 0.0)
        self.assertEqual(sp.snr_fraction(80), 1.0)
        self.assertEqual(sp.snr_fraction(-5), 0.0)
        self.assertEqual(sp.snr_fraction(20), 0.5)


if __name__ == "__main__":
    unittest.main()
