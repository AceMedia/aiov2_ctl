import os
import tempfile
import unittest
from unittest import mock

from ops_agent import preferences, sdr as sdrmod
from ops_agent.sdr import SDR, parse_frequency_mhz, parse_upconverter_arg

try:
    from .sdr_helpers import make_sdr
except ImportError:
    from sdr_helpers import make_sdr


class PanelTests(unittest.TestCase):
    def test_the_panel_presets_match_the_engine(self):
        from ops_gui import sdr_panel
        self.assertEqual(sdr_panel.UPCONVERTER_PRESETS, sdrmod.UPCONVERTER_PRESETS)

    def test_the_panel_does_not_import_the_engine_at_load(self):
        import inspect
        import re
        from ops_gui import sdr_panel
        top = inspect.getsource(sdr_panel).split("def build_panel")[0]
        self.assertIsNone(re.search(r"^\s*(from|import)\s+ops_agent", top, re.M))


class ParseTests(unittest.TestCase):
    def test_limits_move_with_the_offset(self):
        self.assertEqual(sdrmod.tune_limits(0.0), (24.0, 1766.0))
        self.assertEqual(sdrmod.tune_limits(125.0), (0.01, 1641.0))

    def test_hf_needs_an_upconverter(self):
        self.assertIsNone(parse_frequency_mhz("7.1"))
        self.assertEqual(parse_frequency_mhz("7.1", 125.0), 7.1)
        self.assertEqual(parse_frequency_mhz("14.2m", 125.0), 14.2)
        self.assertEqual(parse_frequency_mhz("693k", 125.0), 0.693)

    def test_the_top_of_the_range_shrinks(self):
        self.assertEqual(parse_frequency_mhz("1600", 125.0), 1600.0)
        self.assertIsNone(parse_frequency_mhz("1700", 125.0))
        self.assertEqual(parse_frequency_mhz("1700"), 1700.0)

    def test_still_rejects_junk(self):
        for bad in ("", "abc", "-5", "0", None):
            self.assertIsNone(parse_frequency_mhz(bad, 125.0), bad)

    def test_argument_words(self):
        for word, want in (("off", 0.0), ("none", 0.0), ("0", 0.0), ("ham-it-up", 125.0), ("Ham It Up", 125.0),
                           ("125", 125.0), ("100mhz", 100.0), ("12.5", 12.5)):
            self.assertEqual(parse_upconverter_arg(word), want, word)
        for bad in ("", None, "banana", "-5", "5000"):
            self.assertIsNone(parse_upconverter_arg(bad), bad)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, "preferences.json")
        self.patches = [mock.patch.object(preferences, "PREFERENCES_PATH", path),
                        mock.patch.object(preferences, "PREFERENCES_DIR", self.tmp.name),
                        mock.patch.object(sdrmod, "SDR_MEMORY_PATH", os.path.join(self.tmp.name, "mem.json"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_default_is_no_offset_and_the_old_bands(self):
        s = make_sdr()
        self.assertEqual(s.upconverter_mhz, 0.0)
        names = [p["name"] for p in s.band_profiles()]
        self.assertIn("FM BC", names)
        self.assertNotIn("40m LSB", names)         # unreachable below 24 MHz
        self.assertNotIn("SW 31m", names)

    def test_the_hardware_gets_the_shifted_frequency(self):
        s = make_sdr()
        self.assertEqual(s.hw_mhz(98.0), 98.0)
        s.upconverter_mhz = 125.0
        self.assertEqual(s.hw_mhz(7.1), 132.1)

    def test_set_upconverter_saves_and_retunes_into_range(self):
        s = make_sdr()
        s.freq_mhz = 98.0
        self.assertTrue(s.set_upconverter(125))
        self.assertEqual(preferences.load_preferences()["sdr_upconverter_mhz"], 125.0)
        self.assertEqual(s.pending_freq, 98.0)        # 98 MHz is still in range, so the tune stays where it was
        names = [p["name"] for p in s.band_profiles()]
        for want in ("40m LSB", "20m USB", "SW 31m", "MW AM"):
            self.assertIn(want, names)
        self.assertEqual(sdrmod.load_upconverter_mhz(), 125.0)

    def test_going_back_to_plain_pulls_hf_back_to_fm(self):
        s = make_sdr()
        s.set_upconverter(125)
        s.freq_mhz = 7.1
        s.pending_freq = None
        self.assertTrue(s.set_upconverter(0))
        self.assertEqual(s.pending_freq, 98.0)
        self.assertEqual(s.upconverter_mhz, 0.0)

    def test_bad_values_are_refused(self):
        s = make_sdr()
        for bad in (-1, 5000, "x", None):
            self.assertFalse(s.set_upconverter(bad), bad)
        self.assertEqual(s.upconverter_mhz, 0.0)

    def test_typing_an_hf_frequency(self):
        s = make_sdr()
        self.assertIsNone(s.tune_to_frequency("7.1"))
        s.set_upconverter(125)
        self.assertEqual(s.tune_to_frequency("7.1"), 7.1)
        self.assertEqual(s.pending_freq, 7.1)
        self.assertEqual(s.band_profile_name, "40m LSB")
        self.assertEqual(s.modulation, "LSB")

    def test_band_cycling_with_an_upconverter_includes_short_wave(self):
        s = make_sdr()
        s.set_upconverter(125)
        seen = [s.tune_to_band_profile(1)["name"] for _ in s.band_profiles()]
        self.assertEqual(sorted(seen), sorted(p["name"] for p in s.band_profiles()))
        self.assertIn("SW 49m", seen)

    def test_start_frequency(self):
        s = make_sdr()
        s.last_freq_mhz = None
        self.assertEqual(s.startup_frequency(), 98.0)
        s.upconverter_mhz = 125.0
        self.assertEqual(s.startup_frequency(), 7.1)

    def test_new_hf_profiles_are_sane(self):
        for p in SDR.BAND_PROFILES:
            self.assertLessEqual(p["range_low_mhz"], p["freq_mhz"], p["name"])
            self.assertLessEqual(p["freq_mhz"], p["range_high_mhz"], p["name"])

    def test_nothing_transmits(self):
        import inspect
        src = inspect.getsource(sdrmod).lower()
        for word in ("rtl_tcp -", "tx_power", "transmit(", "hackrf_transfer", "gain -t"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
