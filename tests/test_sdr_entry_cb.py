import json
import os
import tempfile
import unittest
from unittest import mock

from ops_agent import sdr as sdrmod
from ops_agent.sdr import parse_cb_channel

try:
    from .sdr_helpers import make_sdr
except ImportError:
    from sdr_helpers import make_sdr


class ParseCbTests(unittest.TestCase):
    def test_forms(self):
        for text, number in (("ch19", 19), ("CB 9", 9), ("c40", 40), (" ch1 ", 1)):
            self.assertEqual(parse_cb_channel(text), number, text)

    def test_rejects(self):
        for bad in ("", None, "ch0", "ch41", "ch", "19", "chx", "ch1.5"):
            self.assertIsNone(parse_cb_channel(bad), bad)


class CbTuningTests(unittest.TestCase):
    def make(self):
        s = make_sdr()
        s.active = True
        s.request_tune = mock.Mock()
        return s

    def test_typed_channel_defaults_to_uk_27_81(self):
        s = self.make()
        got = s.tune_to_frequency("ch19")
        self.assertAlmostEqual(got, 27.60125 + 18 * 0.01, places=5)
        self.assertEqual(s.band_profile_name, "UK CB FM")
        s.request_tune.assert_called_once()

    def test_typed_channel_follows_the_active_cept_plan(self):
        s = self.make()
        s.apply_band_profile(s._cb_profile("CEPT CB"))
        got = s.tune_to_frequency("ch9")
        self.assertEqual(got, 27.065)
        self.assertEqual(s.band_profile_name, "CEPT CB")

    def test_cb_key_cycles_the_plans(self):
        s = self.make()
        names = []
        for _ in range(4):
            names.append(s.cycle_cb()["name"])
        self.assertEqual(names, ["UK CB FM", "CEPT CB", "CB 11m", "UK CB FM"])

    def test_cb_key_comes_back_to_the_last_plan_used(self):
        s = self.make()
        s.cycle_cb(); s.cycle_cb()          # now on CEPT
        s.apply_band_profile(s._cb_profile("Airband"))
        self.assertEqual(s.cycle_cb()["name"], "CEPT CB")

    def test_inactive_does_nothing(self):
        s = make_sdr()
        s.active = False
        self.assertIsNone(s.cycle_cb())
        self.assertIsNone(s.tune_to_cb_channel(19))


class StartupAndSeekTests(unittest.TestCase):
    def test_start_on_the_last_frequency(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mem.json")
            with open(path, "w") as f:
                json.dump({"bands": {}, "last": 27.78125}, f)
            s = make_sdr(memory_path=path)
            self.assertEqual(s.startup_frequency(), 27.78125)

    def test_default_start_is_fm_and_junk_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mem.json")
            with open(path, "w") as f:
                json.dump({"bands": {}, "last": 5}, f)
            self.assertEqual(make_sdr(memory_path=path).startup_frequency(), 98.0)

    def test_startup_profile_sets_the_modulation(self):
        s = make_sdr()
        profile = s.apply_startup_profile(121.5)
        self.assertEqual(profile["name"], "Airband")
        self.assertEqual(s.modulation, "AM")

    def test_last_frequency_is_saved(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mem.json")
            with mock.patch.object(sdrmod, "SDR_MEMORY_PATH", path):
                s = make_sdr(memory_path=path)
                s._remember_frequency(145.5)
                s.save_band_memory()
            self.assertEqual(json.load(open(path))["last"], 145.5)

    def test_seek_sweeps_and_wraps_inside_the_band(self):
        s = make_sdr()
        s.active = True
        s.request_tune = mock.Mock()
        s.apply_band_profile(s._cb_profile("CEPT CB"))
        s.freq_mhz = 27.40
        s.seek_begin(1)
        self.assertEqual(s.seek_next_window(), 26.965)   # wrapped from the top edge
        s.stop_seek()
        self.assertFalse(s.seek_active)

    def test_seek_gives_up_after_one_full_pass(self):
        s = make_sdr()
        s.active = True
        s.request_tune = mock.Mock()
        s.seek_begin(1)
        while s.seek_next_window() is not None:
            pass
        self.assertFalse(s.seek_active)
        self.assertEqual(s.seek_windows_left, 0)


if __name__ == "__main__":
    unittest.main()
