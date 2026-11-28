"""The in-memory tracker store is bounded in how many addresses it follows, not only by the 48 hours."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_gui import bt_memory as bm  # noqa: E402

FIX = {"mode": 3, "lat": 51.5, "lon": -0.1}


def tracker(i, t):
    return {"addr": "c0:00:00:%02x:%02x:%02x" % (i >> 16 & 255, i >> 8 & 255, i & 255), "kind": "random (private)",
            "last": t, "service_uuids": ["fd5a"]}


class TrackerStoreTests(unittest.TestCase):
    def test_many_changing_addresses_in_a_day_stay_bounded(self):
        travel = bm.Travel()
        count = bm.MAX_TALLIED + 500
        for i in range(count):                       # one new address every 30 s: about 21 hours
            t = 1000.0 + i * 30
            travel.add_trackers({"t": t, "devices": [tracker(i, t)]}, FIX)
        self.assertLessEqual(len(travel.trackers), bm.MAX_TALLIED)
        newest = tracker(count - 1, 0)["addr"]
        self.assertIn(newest, travel.trackers, "the ones heard most recently are kept")

    def test_sightings_carry_on_after_the_clock_jumps_back_an_hour(self):
        travel = bm.Travel()
        t0 = 100000.0
        for i in range(3):
            t = t0 + i * 60
            travel.add_trackers({"t": t, "devices": [tracker(7, t)]}, FIX)
        back = t0 + 120 - 3600                       # a GPS time correction sets the clock back an hour
        for i in range(3):
            t = back + i * 60
            travel.add_trackers({"t": t, "devices": [tracker(7, t)]}, FIX)
        sightings = travel.trackers[tracker(7, 0)["addr"]]["sightings"]
        self.assertEqual(len(sightings), 6)
        self.assertEqual(sightings[-1][0], back + 120, "the earlier time is taken as the new latest")
        t = back + 130                               # still at most one sighting per 30 seconds afterwards
        travel.add_trackers({"t": t, "devices": [tracker(7, t)]}, FIX)
        self.assertEqual(len(sightings), 6)

    def test_the_48_hours_still_forget(self):
        travel = bm.Travel()
        travel.add_trackers({"t": 1000.0, "devices": [tracker(1, 1000.0)]}, FIX)
        later = 1000.0 + bm.TRACKER_KEEP + 1
        travel.add_trackers({"t": later, "devices": [tracker(2, later)]}, FIX)
        self.assertEqual(list(travel.trackers), [tracker(2, 0)["addr"]])


if __name__ == "__main__":
    unittest.main()

