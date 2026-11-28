import unittest

from ops_gui import bt_memory as m
from ops_gui import bt_panel as bt
from ops_gui import notify

T0 = 1_790_000_000.0
LAT, LON = 51.5, -0.1
DEG_PER_M = 1 / 111195.0                     # degrees of latitude per metre on the mean Earth radius
TAG = "d1:22:33:44:55:66"


def north(metres):
    return LAT + metres * DEG_PER_M


def walk(n=3, minutes=31, apart=150):
    """n sightings evenly over `minutes`, each `apart` metres north of the one before."""
    step = minutes * 60.0 / (n - 1)
    return [(T0 + i * step, north(i * apart), LON) for i in range(n)]


def fix(lat=LAT, lon=LON):
    return {"class": "TPV", "mode": 3, "lat": lat, "lon": lon}


def tag(t, addr=TAG, airtag=True):
    d = {"addr": addr, "kind": "random (fixed)", "name": "", "maker": "", "rssi": -70, "best": -70, "count": 1,
         "last": t, "services": []}
    if airtag:
        d["mfr_data"] = {"004c": "1219000000"}
    return {"t": t, "devices": [d]}


class RuleTests(unittest.TestCase):
    def test_the_thresholds_are_the_ones_kismet_documents(self):
        self.assertEqual((m.TRACKER_MINUTES, m.TRACKER_PLACES, m.TRACKER_METRES), (30, 3, 100.0))

    def test_three_places_over_thirty_one_minutes_meets_it_with_the_facts(self):
        facts = m.seen_at_several_places(walk())
        self.assertEqual(facts["places"], 3)
        self.assertEqual((facts["first"], facts["last"]), (T0, T0 + 31 * 60))
        self.assertAlmostEqual(facts["minutes"], 31.0)

    def test_just_under_and_just_over_thirty_minutes(self):
        self.assertIsNone(m.seen_at_several_places(walk(minutes=29.99)))
        self.assertIsNotNone(m.seen_at_several_places(walk(minutes=30)))
        self.assertIsNotNone(m.seen_at_several_places(walk(minutes=30.01)))

    def test_just_under_and_just_over_one_hundred_metres(self):
        self.assertIsNone(m.seen_at_several_places(walk(apart=99.5)))
        self.assertIsNotNone(m.seen_at_several_places(walk(apart=100.5)))

    def test_two_places_is_not_enough_and_three_is(self):
        self.assertIsNone(m.seen_at_several_places(walk(n=2, minutes=120)))
        self.assertEqual(m.seen_at_several_places(walk(n=3))["places"], 3)
        self.assertEqual(m.seen_at_several_places(walk(n=5))["places"], 5)

    def test_order_does_not_matter(self):
        s = walk(n=4)
        self.assertEqual(m.seen_at_several_places(list(reversed(s))), m.seen_at_several_places(s))
        self.assertEqual(m.seen_at_several_places([s[2], s[0], s[3], s[1]]), m.seen_at_several_places(s))

    def test_going_back_and_forth_between_two_places_is_two_places(self):
        s = [(T0 + i * 600, north(0 if i % 2 == 0 else 500), LON) for i in range(8)]
        self.assertIsNone(m.seen_at_several_places(s))
        self.assertEqual(m.seen_at_several_places(s, min_places=2)["places"], 2)

    def test_a_point_between_two_places_closer_than_100_m_to_either_is_not_new(self):
        s = [(T0, north(0), LON), (T0 + 900, north(150), LON), (T0 + 1900, north(75), LON)]
        self.assertIsNone(m.seen_at_several_places(s))
        self.assertEqual(m.seen_at_several_places(s, min_places=2)["places"], 2)

    def test_no_fixes_at_all_gives_no_places(self):
        s = [(T0 + i * 600, None, None) for i in range(10)]
        self.assertIsNone(m.seen_at_several_places(s))
        self.assertIsNone(m.seen_at_several_places([]))
        self.assertIsNone(m.seen_at_several_places(None))

    def test_sightings_without_a_fix_still_count_for_the_time(self):
        s = [(T0, north(0), LON), (T0 + 60, north(200), LON), (T0 + 120, north(400), LON), (T0 + 1900, None, None)]
        facts = m.seen_at_several_places(s)
        self.assertEqual((facts["places"], facts["last"]), (3, T0 + 1900))

    def test_identical_points_are_one_place(self):
        s = [(T0 + i * 600, LAT, LON) for i in range(10)]
        self.assertIsNone(m.seen_at_several_places(s))
        self.assertEqual(m.seen_at_several_places(s, min_places=1)["places"], 1)

    def test_broken_sightings_are_left_out(self):
        s = walk() + [("soon", LAT, LON), (float("nan"), LAT, LON), (True, LAT, LON), (T0 + 10,)]
        self.assertEqual(m.seen_at_several_places(s), m.seen_at_several_places(walk()))


class TravelTrackerTests(unittest.TestCase):
    def feed(self, travel, minutes=40, every=60, apart=150, airtag=True, gps=True):
        for i in range(int(minutes * 60 / every) + 1):
            t = T0 + i * every
            travel.add(tag(t, airtag=airtag), fix(north(i * every / 60 * apart / 10)) if gps else {})
        return travel

    def test_a_tracker_heard_while_moving_meets_the_rule(self):
        travel = self.feed(m.Travel())
        facts = travel.tracker_facts(TAG)
        self.assertGreaterEqual(facts["places"], 3)
        self.assertGreaterEqual(facts["minutes"], 30)

    def test_something_that_is_not_a_tracker_is_never_noted(self):
        travel = self.feed(m.Travel(), airtag=False)
        self.assertEqual(travel.trackers, {})

    def test_without_a_fix_nothing_is_noted(self):
        travel = self.feed(m.Travel(), gps=False)
        self.assertEqual(travel.trackers, {})
        self.assertIsNone(travel.tracker_facts(TAG))

    def test_sightings_are_thinned_and_bounded(self):
        travel = m.Travel()
        for i in range(200):
            travel.add(tag(T0 + i * 5), fix())
        self.assertEqual(len(travel.trackers[TAG]["sightings"]), len(range(0, 1000, m.SIGHTING_EVERY)))
        for i in range(2000):
            travel.add(tag(T0 + 20000 + i * m.SIGHTING_EVERY), fix())
        self.assertEqual(len(travel.trackers[TAG]["sightings"]), m.MAX_SIGHTINGS)

    def test_an_address_not_heard_for_48_hours_is_forgotten(self):
        travel = self.feed(m.Travel())
        other = "d1:00:00:00:00:01"
        travel.add(tag(T0 + 40 * 60 + m.TRACKER_KEEP - 1, other), fix())
        self.assertIn(TAG, travel.trackers)
        travel.add(tag(T0 + 40 * 60 + m.TRACKER_KEEP + 1, other), fix())
        self.assertNotIn(TAG, travel.trackers)

    def test_clear_forgets_the_sightings(self):
        travel = self.feed(m.Travel())
        travel.clear()
        self.assertEqual(travel.trackers, {})

    def test_each_address_is_handed_out_once(self):
        travel = self.feed(m.Travel())
        told = travel.trackers_to_tell()
        self.assertEqual([a for a, _ in told], [TAG])
        self.assertEqual(travel.trackers_to_tell(), [])

    def test_the_row_carries_a_short_mark_and_the_facts(self):
        travel = self.feed(m.Travel())
        row = bt.device_rows(tag(T0 + 40 * 60), travel=travel, session_start=T0)[0]
        facts = travel.tracker_facts(TAG)
        self.assertTrue(row["known"].endswith(m.tracker_mark(facts)), row["known"])
        tip = bt.known_tooltip(tag(T0)["devices"][0], travel)
        self.assertTrue(tip.endswith(m.tracker_text(facts)), tip)
        plain = bt.device_rows(tag(T0), travel=m.Travel(), session_start=T0)[0]
        self.assertNotIn("min", plain["known"])
        self.assertEqual(bt.known_tooltip(tag(T0)["devices"][0], m.Travel()), m.known_tip("random (fixed)"))


class WordingTests(unittest.TestCase):
    FACTS = {"first": T0, "last": T0 + 47 * 60, "minutes": 47.3, "places": 3}

    def every_sentence(self):
        return [m.tracker_mark(self.FACTS), m.tracker_text(self.FACTS), m.TRACKER_HELP,
                *m.describe_tracker(TAG, self.FACTS)[1:3], dict(notify.SOURCES)[m.TRACKER_SOURCE]]

    def test_the_tooltip_says_exactly_this(self):
        self.assertEqual(m.tracker_text(self.FACTS),
                         "This tracker's address has been heard for 47 minutes at 3 places at least 100 m apart "
                         "(this computer's own positions). It may belong to someone travelling the same way. "
                         "A tracker that changes its address is not noticed by this check.")
        self.assertEqual(m.tracker_mark(self.FACTS), "3 places in 47 min")

    def test_nothing_shown_claims_what_was_not_measured(self):
        for text in self.every_sentence():
            low = text.lower()
            for word in ("following", "stalking", "tracking you", "away", "located", "where it is", "direction",
                         "person", "suspicious"):
                self.assertNotIn(word, low, text)

    def test_the_help_and_the_notification_say_changing_addresses_are_not_noticed(self):
        self.assertIn(m.TRACKER_CAVEAT, m.TRACKER_HELP)
        self.assertIn("Address changes are not noticed.", m.describe_tracker(TAG, self.FACTS)[2])

    def test_the_notification_is_plain_fits_and_starts_off(self):
        source, _title, body, level = m.describe_tracker(TAG, self.FACTS)
        self.assertEqual((source, level), ("bttrackers", "info"))
        self.assertLessEqual(len(body), 160)
        self.assertIn(source, dict(notify.SOURCES))
        self.assertFalse(notify.DEFAULTS[source])
        self.assertFalse(notify.config({})[source])


if __name__ == "__main__":
    unittest.main()

