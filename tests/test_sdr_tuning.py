import json
import os
import tempfile
import time
import unittest
from unittest import mock

from ops_agent import sdr as sdrmod
from ops_agent.sdr import SDR, level_bar, parse_frequency_mhz

try:
    from .sdr_helpers import make_sdr
except ImportError:
    from sdr_helpers import make_sdr


class ParseFrequencyTests(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_frequency_mhz("98.5"), 98.5)
        self.assertEqual(parse_frequency_mhz("98.5m"), 98.5)
        self.assertEqual(parse_frequency_mhz("145.5 MHz"), 145.5)
        self.assertEqual(parse_frequency_mhz("145500k"), 145.5)
        self.assertEqual(parse_frequency_mhz("145500"), 145.5)
        self.assertEqual(parse_frequency_mhz("145500000hz"), 145.5)
        self.assertEqual(parse_frequency_mhz("1.09g"), 1090.0)
        self.assertEqual(parse_frequency_mhz("446.00625"), 446.00625)

    def test_rejects_junk_and_out_of_range(self):
        for bad in ("", None, "abc", "98.5.1", "-5", "3", "2000", "5m", "99999999999"):
            self.assertIsNone(parse_frequency_mhz(bad), bad)


class BandProfileTests(unittest.TestCase):
    def test_profiles_are_sane_and_unique(self):
        names = [p["name"] for p in SDR.BAND_PROFILES]
        self.assertEqual(len(names), len(set(names)))
        for p in SDR.BAND_PROFILES:
            self.assertLessEqual(p["range_low_mhz"], p["freq_mhz"], p["name"])
            self.assertLessEqual(p["freq_mhz"], p["range_high_mhz"], p["name"])
            self.assertIn(p["modulation"], ("WFM", "FM", "NFM", "AM", "DSB", "USB", "LSB", "CW"))

    def test_original_profiles_keep_their_order(self):
        names = [p["name"] for p in SDR.BAND_PROFILES]
        self.assertEqual(names[:10], ["FM BC", "Airband", "NOAA WX", "2m Ham", "70cm Ham", "UK CB FM", "CEPT CB", "CB 11m", "10m USB", "40m LSB"])
        for wanted in ("Marine VHF", "PMR446"):
            self.assertIn(wanted, names)

    def test_pmr446_plan(self):
        ch = SDR.PMR446_CHANNELS
        self.assertEqual(len(ch), 16)
        self.assertAlmostEqual(ch[0]["freq_mhz"], 446.00625)
        self.assertAlmostEqual(ch[15]["freq_mhz"], 446.19375)

    def test_band_cycling_visits_every_band_once(self):
        s = make_sdr()
        seen = []
        for _ in s.band_profiles():
            seen.append(s.tune_to_band_profile(1)["name"])
        self.assertEqual(sorted(seen), sorted(p["name"] for p in s.band_profiles()))


class TuneEntryTests(unittest.TestCase):
    def test_typed_frequency_selects_band_and_mode(self):
        s = make_sdr()
        self.assertEqual(s.tune_to_frequency("121.5"), 121.5)
        self.assertEqual(s.modulation, "AM")
        self.assertEqual(s.band_profile_name, "Airband")
        self.assertEqual(s.pending_freq, 121.5)

    def test_bad_entry_changes_nothing(self):
        s = make_sdr()
        before = (s.modulation, s.pending_freq)
        self.assertIsNone(s.tune_to_frequency("banana"))
        self.assertEqual((s.modulation, s.pending_freq), before)

    def test_inactive_sdr_ignores_entry(self):
        s = make_sdr(stderr="No supported devices found.")
        self.assertIsNone(s.tune_to_frequency("98.5"))


class BandMemoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "sdr_memory.json")

    def test_remembers_per_band_and_returns_to_it(self):
        with mock.patch.object(sdrmod, "SDR_MEMORY_PATH", self.path):
            s = make_sdr(memory_path=self.path)
            s.tune_to_frequency("123.45")
            s.last_tune_request = 0
            with mock.patch.object(s, "start_scan", return_value=True):  # never spawn rtl_sdr in tests
                s.apply_pending_tune()
            self.assertEqual(s.band_memory.get("Airband"), 123.45)
            fm = next(p for p in SDR.BAND_PROFILES if p["name"] == "FM BC")
            s.apply_band_profile(fm)
            air = next(p for p in SDR.BAND_PROFILES if p["name"] == "Airband")
            s.apply_band_profile(air)
            self.assertEqual(s.pending_freq, 123.45)

    def test_saved_and_reloaded(self):
        with mock.patch.object(sdrmod, "SDR_MEMORY_PATH", self.path):
            s = make_sdr(memory_path=self.path)
            s.band_memory["Marine VHF"] = 156.8
            s._memory_dirty = True
            s.shutdown()
            with open(self.path) as handle:
                self.assertEqual(json.load(handle)["bands"]["Marine VHF"], 156.8)
            again = make_sdr(memory_path=self.path)
            self.assertEqual(again.band_memory["Marine VHF"], 156.8)

    def test_bad_or_out_of_range_memory_ignored(self):
        with open(self.path, "w") as handle:
            json.dump({"bands": {"Airband": 50.0, "FM BC": "x", "Nope": 1}}, handle)
        s = make_sdr(memory_path=self.path)
        self.assertEqual(s.band_memory, {})

    def test_corrupt_file_ignored(self):
        with open(self.path, "w") as handle:
            handle.write("{not json")
        s = make_sdr(memory_path=self.path)
        self.assertEqual(s.band_memory, {})


class SquelchTests(unittest.TestCase):
    def sdr(self, level=30):
        s = make_sdr()
        s.squelch_level = level
        s.audio_noise_est = -50.0
        return s

    def test_off_is_always_open(self):
        s = self.sdr(0)
        self.assertTrue(s._squelch_update(-90, now=1.0))

    def test_opens_above_threshold_and_closes_after_hang(self):
        s = self.sdr(30)  # opens at -40
        self.assertFalse(s._squelch_update(-45, now=100.0))
        self.assertTrue(s._squelch_update(-35, now=101.0))
        self.assertTrue(s._squelch_update(-50, now=101.1))   # inside hang time
        self.assertFalse(s._squelch_update(-50, now=102.0))  # hang over

    def test_hysteresis_keeps_it_open_on_small_dips(self):
        s = self.sdr(30)
        s._squelch_update(-35, now=1.0)
        self.assertTrue(s._squelch_update(-41, now=5.0))   # between close and open
        self.assertFalse(s._squelch_update(-43, now=9.0))


class LevelTests(unittest.TestCase):
    def test_level_bar(self):
        self.assertEqual(level_bar(None, 4), "░" * 4)
        self.assertEqual(level_bar(40, 4), "█" * 4)
        self.assertEqual(level_bar(20, 4), "██░░")
        self.assertEqual(level_bar(-5, 4), "░" * 4)
        self.assertEqual(level_bar(99, 4), "█" * 4)

    def test_snr(self):
        s = make_sdr()
        self.assertIsNone(s.snr_db())
        s.spectrum = [10.0] * 80 + [30.0] * 20
        s.s_meter_db = 25.0
        self.assertEqual(s.snr_db(), 15.0)


if __name__ == "__main__":
    unittest.main()
