"""The several-places check must not count a position that is not a real one (NaN, or exactly 0,0)."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_gui import bt_memory as bm  # noqa: E402


def snap(t, addr="aa:bb:cc:dd:ee:01"):
    return {"t": t, "devices": [{"addr": addr, "kind": "public", "last": t, "rssi": -60}]}


def fix(lat, lon):
    return {"mode": 3, "lat": lat, "lon": lon}


class BadPositionTests(unittest.TestCase):
    def test_nan_positions_are_not_places(self):
        travel = bm.Travel()
        for i in range(5):
            travel.add(snap(1000.0 + i * 300), fix(float("nan"), float("nan")))
        self.assertEqual(travel.flagged(), [])
        self.assertEqual(travel.places("aa:bb:cc:dd:ee:01"), 0)

    def test_a_zero_zero_position_is_not_a_place(self):
        travel = bm.Travel()
        positions = [fix(51.5, -0.1), fix(0.0, 0.0), fix(51.51, -0.1), fix(0.0, 0.0)]
        for i, tpv in enumerate(positions):
            travel.add(snap(1000.0 + i * 300), tpv)
        self.assertEqual(travel.places("aa:bb:cc:dd:ee:01"), 2)
        self.assertEqual(travel.flagged(), [])

    def test_usable_fix_refuses_them_and_keeps_real_ones(self):
        self.assertFalse(bm.usable_fix(fix(float("nan"), 1.0)))
        self.assertFalse(bm.usable_fix(fix(1.0, float("inf"))))
        self.assertFalse(bm.usable_fix(fix(0.0, 0.0)))
        self.assertTrue(bm.usable_fix(fix(0.0, 10.0)), "the equator alone is a real place")
        self.assertTrue(bm.usable_fix(fix(51.5, -0.1)))
        self.assertFalse(bm.usable_fix(fix("51.5", -0.1)))


if __name__ == "__main__":
    unittest.main()

