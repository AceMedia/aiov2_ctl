import json
import os
import stat
import tempfile
import time
import unittest
from unittest import mock

from ops_gui import bt_memory as m
from ops_gui import bt_panel as bt
from ops_gui import notify, reports

PUBLIC, FIXED, PRIVATE = "11:22:33:44:55:66", "c6:05:04:03:02:01", "46:05:04:03:02:01"
NOON = time.mktime((2026, 11, 20, 12, 0, 0, 0, 0, -1))        # local noon, so a day later is the next local day
DAY = 86400
STEP = 0.004                                                   # degrees of latitude: about 445 m
FORBIDDEN = ("track", "follow", "stalk", "person", "owner", "belong", "spy", "suspicious")


def fix(lat=51.5, lon=-0.1, **extra):
    return dict({"class": "TPV", "mode": 3, "lat": lat, "lon": lon}, **extra)


def snap(t, addr=PUBLIC, kind="public", rssi=-60, name="Kitchen Scale", maker="Acme", last=None):
    return {"t": t, "devices": [{"addr": addr, "kind": kind, "name": name, "maker": maker, "rssi": rssi,
                                 "best": rssi, "count": 1, "last": t if last is None else last, "services": []}]}


def walked(places=4, minutes=23, rssis=(-70, -45, -62, -80)):
    """A Travel that heard PUBLIC at `places` spots about 445 m apart, spread evenly over `minutes`."""
    travel = m.Travel()
    met = []
    for i in range(places):
        t = NOON + i * minutes * 60.0 / (places - 1)
        met += travel.add(snap(t, rssi=rssis[i % len(rssis)]), fix(51.5 + i * STEP))
    return travel, met


class RememberableTests(unittest.TestCase):
    def test_public_and_fixed_random_addresses_can_be_remembered(self):
        self.assertTrue(m.rememberable("public"))
        self.assertTrue(m.rememberable("random (fixed)"))

    def test_private_and_unknown_kinds_cannot(self):
        for kind in ("private (changes)", "random", "", None, 5, "PUBLIC"):
            self.assertFalse(m.rememberable(kind), kind)

    def test_an_address_is_normalised_or_refused(self):
        self.assertEqual(m.normalise(" 11:22:33:44:55:AA "), "11:22:33:44:55:aa")
        for bad in ("", "112233445566", "11:22:33:44:55", None, 7, "zz:22:33:44:55:66"):
            self.assertEqual(m.normalise(bad), "")


class MemoryTests(unittest.TestCase):
    def test_the_first_hearing_is_new_and_the_second_is_not(self):
        mem = m.Memory()
        self.assertTrue(mem.see(PUBLIC, "public", "Scale", "Acme", NOON))
        self.assertFalse(mem.see(PUBLIC, "public", "Scale", "Acme", NOON + 5))
        self.assertEqual(mem.known(PUBLIC)["first"], NOON)
        self.assertEqual(mem.known(PUBLIC)["last"], NOON + 5)

    def test_a_private_address_is_never_stored(self):
        mem = m.Memory()
        self.assertFalse(mem.see(PRIVATE, "private (changes)", "Phone", "Apple", NOON))
        self.assertEqual(mem.see_snapshot(snap(NOON, PRIVATE, "private (changes)")), [])
        self.assertEqual(mem.entries, {})
        self.assertFalse(mem.dirty)
        self.assertNotIn(PRIVATE, mem.to_json())

    def test_a_malformed_address_is_never_stored(self):
        mem = m.Memory()
        mem.see("not an address", "public", when=NOON)
        mem.see(None, "public", when=NOON)
        self.assertEqual(mem.entries, {})

    def test_hearing_it_many_times_in_one_day_counts_one_day(self):
        mem = m.Memory()
        for i in range(50):
            mem.see(PUBLIC, "public", when=NOON + i * 60)
        self.assertEqual(mem.known(PUBLIC)["days"], 1)

    def test_each_different_day_counts_once(self):
        mem = m.Memory()
        for day in (0, 0, 1, 1, 1, 5):
            mem.see(PUBLIC, "public", when=NOON + day * DAY)
            mem.see(PUBLIC, "public", when=NOON + day * DAY + 30)
        self.assertEqual(mem.known(PUBLIC)["days"], 3)
        self.assertEqual(mem.known(PUBLIC)["day"], m.day_of(NOON + 5 * DAY))

    def test_a_clock_that_goes_backwards_does_not_add_a_day(self):
        mem = m.Memory()
        mem.see(PUBLIC, "public", when=NOON)
        mem.see(PUBLIC, "public", when=NOON - 3 * DAY)
        self.assertEqual(mem.known(PUBLIC)["days"], 1)
        self.assertEqual(mem.known(PUBLIC)["last"], NOON)

    def test_a_name_heard_later_is_kept_and_a_missing_one_does_not_erase_it(self):
        mem = m.Memory()
        mem.see(PUBLIC, "public", "", "", NOON)
        mem.see(PUBLIC, "public", "Scale", "Acme", NOON + 1)
        mem.see(PUBLIC, "public", "", "", NOON + 2)
        self.assertEqual((mem.known(PUBLIC)["name"], mem.known(PUBLIC)["maker"]), ("Scale", "Acme"))

    def test_a_snapshot_feeds_every_rememberable_device_and_reports_the_new_ones(self):
        mem = m.Memory()
        both = {"t": NOON, "devices": snap(NOON)["devices"] + snap(NOON, FIXED, "random (fixed)")["devices"]
                + snap(NOON, PRIVATE, "private (changes)")["devices"]}
        self.assertEqual(sorted(mem.see_snapshot(both)), sorted([PUBLIC, FIXED]))
        self.assertEqual(mem.see_snapshot(both), [])

    def test_a_snapshot_uses_when_the_device_was_last_heard_not_when_the_snapshot_was_made(self):
        mem = m.Memory()
        mem.see_snapshot(snap(NOON + 500, last=NOON + 20))
        self.assertEqual(mem.known(PUBLIC)["last"], NOON + 20)

    def test_marking_as_mine_and_taking_the_mark_off(self):
        mem = m.Memory()
        mem.see(PUBLIC, "public", when=NOON)
        mem.dirty = False
        self.assertTrue(mem.set_mine(PUBLIC))
        self.assertTrue(mem.is_mine(PUBLIC) and mem.dirty)
        self.assertFalse(mem.set_mine(PUBLIC))
        self.assertTrue(mem.set_mine(PUBLIC, False))
        self.assertFalse(mem.is_mine(PUBLIC))

    def test_an_address_never_heard_cannot_be_marked(self):
        mem = m.Memory()
        self.assertFalse(mem.set_mine(PRIVATE))
        self.assertEqual(mem.entries, {})


class FileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "state", "bluetooth_memory.json")

    def test_it_is_written_private_and_read_back_the_same(self):
        mem = m.Memory()
        mem.see(PUBLIC, "public", "Scale", "Acme", NOON)
        mem.see(PUBLIC, "public", "Scale", "Acme", NOON + DAY)
        mem.set_mine(PUBLIC)
        self.assertTrue(mem.save(self.path))
        self.assertFalse(mem.dirty)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        back = m.Memory().load(self.path)
        self.assertEqual(back.entries, mem.entries)
        self.assertEqual(back.known(PUBLIC)["days"], 2)
        self.assertTrue(back.is_mine(PUBLIC))

    def test_a_file_that_was_readable_by_others_is_made_private_again(self):
        os.makedirs(os.path.dirname(self.path))
        for name in (self.path, self.path + ".tmp"):
            with open(name, "w") as f:
                f.write("{}")
            os.chmod(name, 0o644)
        mem = m.Memory()
        mem.see(PUBLIC, "public", when=NOON)
        mem.save(self.path)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_a_missing_file_leaves_it_empty(self):
        self.assertEqual(m.Memory().load(self.path).entries, {})

    def test_a_damaged_file_leaves_it_empty_and_never_raises(self):
        os.makedirs(os.path.dirname(self.path))
        for junk in ("", "{", "[1, 2]", "null", '{"devices": 5}', '{"devices": [1]}', "\x00\x01 not json"):
            with open(self.path, "w") as f:
                f.write(junk)
            self.assertEqual(m.Memory().load(self.path).entries, {}, junk)

    def test_a_damaged_entry_is_dropped_and_the_rest_kept(self):
        os.makedirs(os.path.dirname(self.path))
        good = {"name": "Scale", "maker": "Acme", "kind": "public", "first": NOON, "last": NOON + 9, "days": 2,
                "day": m.day_of(NOON + 9), "mine": True}
        data = {"version": 1, "devices": {PUBLIC: good, FIXED: "junk", "nonsense": good, PRIVATE: {"first": "x"},
                                           "aa:bb:cc:dd:ee:ff": {"first": NOON, "last": "soon", "days": -4,
                                                                 "name": 9, "mine": "yes"}}}
        with open(self.path, "w") as f:
            json.dump(data, f)
        mem = m.Memory().load(self.path)
        self.assertEqual(sorted(mem.entries), sorted([PUBLIC, "aa:bb:cc:dd:ee:ff"]))
        self.assertEqual(mem.known(PUBLIC), good)
        mended = mem.known("aa:bb:cc:dd:ee:ff")
        self.assertEqual((mended["last"], mended["days"], mended["name"], mended["mine"]), (NOON, 1, "", False))
        mem.see("aa:bb:cc:dd:ee:ff", "public", when=NOON + DAY)      # and it still works afterwards
        self.assertEqual(mended["days"], 2)

    def test_the_file_is_bounded_and_the_stalest_are_dropped(self):
        mem = m.Memory()
        for i in range(m.MAX_ENTRIES + 50):
            mem.see("00:00:00:00:%02x:%02x" % (i // 256, i % 256), "public", when=NOON + i)
        mem.save(self.path)
        back = m.Memory().load(self.path)
        self.assertEqual(len(back.entries), m.MAX_ENTRIES)
        self.assertNotIn("00:00:00:00:00:00", back.entries)                     # the oldest went
        self.assertIn("00:00:00:00:%02x:%02x" % ((m.MAX_ENTRIES + 49) // 256, (m.MAX_ENTRIES + 49) % 256), back.entries)

    def test_one_marked_as_mine_is_kept_however_stale(self):
        entries = {"00:00:00:00:00:%02x" % i: {"last": i, "mine": i == 0} for i in range(10)}
        kept = m.trimmed(entries, limit=3)
        self.assertEqual(sorted(kept), ["00:00:00:00:00:00", "00:00:00:00:00:08", "00:00:00:00:00:09"])

    def test_a_save_that_cannot_be_written_does_not_raise_and_stays_due(self):
        mem = m.Memory()
        mem.see(PUBLIC, "public", when=NOON)
        blocker = os.path.join(self.dir.name, "file")
        open(blocker, "w").close()
        self.assertFalse(mem.save(os.path.join(blocker, "under", "memory.json")))
        self.assertTrue(mem.dirty)

    def test_a_timed_save_is_due_only_when_something_changed_and_time_has_passed(self):
        mem = m.Memory()
        self.assertFalse(mem.due(NOON))
        mem.see(PUBLIC, "public", when=NOON)
        self.assertTrue(mem.due(NOON))
        text = mem.text_to_save(NOON)
        self.assertIn(PUBLIC, json.loads(text)["devices"])
        mem.see(PUBLIC, "public", when=NOON + 10)
        self.assertFalse(mem.due(NOON + 10))
        self.assertTrue(mem.due(NOON + m.SAVE_EVERY))

    def test_the_file_lives_in_the_private_state_folder(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.dir.name}):
            self.assertEqual(m.memory_path(), os.path.join(reports.state_dir(), "bluetooth_memory.json"))

    def test_clear_local_data_removes_the_file_and_tells_open_pages(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.dir.name}):
            mem = m.Memory()
            mem.see(PUBLIC, "public", when=NOON)
            mem.save()
            self.assertTrue(os.path.exists(m.memory_path()))
            before = m.cleared()
            self.assertEqual(reports.clear_all(), 1)
            self.assertFalse(os.path.exists(m.memory_path()))
            self.assertEqual(m.cleared(), before + 1)
            self.assertEqual(reports.clear_all(), 0)

    def test_nothing_remembered_is_put_in_a_problem_report(self):
        import inspect
        source = inspect.getsource(reports.build_report)
        self.assertNotIn("bt_memory", source)
        self.assertNotIn("bluetooth", source.lower())


class KnownColumnTests(unittest.TestCase):
    def entry(self, first, last, days, mine=False):
        return {"first": first, "last": last, "days": days, "day": m.day_of(last), "mine": mine}

    def test_first_heard_in_this_scan_is_new(self):
        self.assertEqual(m.known_text("public", self.entry(NOON + 5, NOON + 60, 1), NOON + 60, NOON), "new")
        self.assertEqual(m.known_text("public", None, NOON, NOON), "new")

    def test_heard_earlier_today_only_is_seen_today(self):
        self.assertEqual(m.known_text("public", self.entry(NOON - 3600, NOON + 5, 1), NOON + 5, NOON), "seen today")

    def test_heard_on_other_days_says_how_many(self):
        self.assertEqual(m.known_text("random (fixed)", self.entry(NOON - 9 * DAY, NOON, 4), NOON, NOON), "seen on 4 days")
        self.assertEqual(m.known_text("public", self.entry(NOON - 2 * DAY, NOON, 2), NOON, NOON), "seen on 2 days")

    def test_heard_once_on_an_earlier_day_and_not_since_says_one_day(self):
        self.assertEqual(m.known_text("public", self.entry(NOON - DAY, NOON - DAY, 1), NOON, NOON), "seen on 1 day")

    def test_a_private_address_says_it_changes_and_an_unknown_kind_says_nothing(self):
        self.assertEqual(m.known_text("private (changes)", None, NOON, NOON), "changes")
        self.assertEqual(m.known_text("random", None, NOON, NOON), "")
        self.assertEqual(m.known_text("", None, NOON, NOON), "")

    def test_mine_and_the_number_of_places_are_added(self):
        self.assertEqual(m.known_text("public", self.entry(NOON - DAY, NOON, 2, mine=True), NOON, NOON, places=3),
                         "seen on 2 days, mine, 3 places")
        self.assertEqual(m.known_text("public", self.entry(NOON - DAY, NOON, 2), NOON, NOON, places=1), "seen on 2 days")

    def test_the_tooltip_and_the_line_say_why_a_private_address_is_not_remembered(self):
        self.assertIn("changes every few minutes", m.known_tip("private (changes)"))
        self.assertIn("private file", m.known_tip("public"))
        line = m.pick_line(PRIVATE, "private (changes)", "", False)
        self.assertIn("changes every few minutes", line)
        self.assertIn("cannot be marked", line)
        self.assertIn("marked as yours", m.pick_line(PUBLIC, "public", "Scale", True))
        self.assertEqual(m.pick_line(PUBLIC, "public", "", False), PUBLIC + " picked.")

    def test_the_table_has_a_known_column_filled_from_the_memory(self):
        self.assertIn(("Known", "known"), bt.COLUMNS)
        mem = m.Memory()
        mem.see(PUBLIC, "public", when=NOON - 3 * DAY)
        mem.see(PUBLIC, "public", when=NOON - DAY)
        s = {"t": NOON, "devices": snap(NOON)["devices"] + snap(NOON, FIXED, "random (fixed)")["devices"]
             + snap(NOON, PRIVATE, "private (changes)")["devices"]}
        mem.see_snapshot(s)
        rows = bt.device_rows(s, memory=mem, travel=m.Travel(), session_start=NOON - 1)
        self.assertEqual([r["known"] for r in rows], ["seen on 3 days", "new", "changes"])
        for row in rows:
            self.assertEqual(set(row), {key for _, key in bt.COLUMNS})

    def test_a_row_stays_new_for_the_whole_scan_and_across_its_restarts(self):
        mem = m.Memory()
        mem.see_snapshot(snap(NOON + 2))
        later = snap(NOON + 1500)                                   # two ten minute restarts on
        mem.see_snapshot(later)
        self.assertEqual(bt.device_rows(later, memory=mem, session_start=NOON)[0]["known"], "new")
        self.assertEqual(bt.device_rows(later, memory=mem, session_start=NOON + 1400)[0]["known"], "seen today")


class DistanceTests(unittest.TestCase):
    def test_haversine_matches_known_distances(self):
        self.assertAlmostEqual(m.haversine_m(0, 0, 0, 1), 111195, delta=5)          # a degree along the equator
        self.assertAlmostEqual(m.haversine_m(51.5, -0.1, 51.501, -0.1), 111.2, delta=0.2)
        self.assertAlmostEqual(m.haversine_m(51.5074, -0.1278, 48.8566, 2.3522), 343500, delta=1500)   # London-Paris
        self.assertEqual(m.haversine_m(51.5, -0.1, 51.5, -0.1), 0.0)

    def test_haversine_is_the_same_both_ways_and_across_the_date_line(self):
        self.assertAlmostEqual(m.haversine_m(10, 20, 11, 21), m.haversine_m(11, 21, 10, 20))
        self.assertAlmostEqual(m.haversine_m(0, 179.5, 0, -179.5), 111195, delta=5)

    def test_a_position_close_to_a_known_place_is_the_same_place(self):
        places = []
        self.assertTrue(m.add_place(places, 51.5, -0.1))
        self.assertFalse(m.add_place(places, 51.5 + 0.0026, -0.1))     # about 289 m
        self.assertTrue(m.add_place(places, 51.5 + 0.0028, -0.1))      # about 311 m
        self.assertEqual(len(places), 2)

    def test_a_new_place_must_be_far_from_every_place_already_there(self):
        places = [(51.5, -0.1), (51.5 + STEP, -0.1)]
        self.assertFalse(m.add_place(places, 51.5 + STEP / 2, -0.1))   # 222 m from both
        self.assertEqual(len(places), 2)

    def test_standing_still_with_a_jittery_fix_is_one_place(self):
        places = []
        for i in range(200):
            m.add_place(places, 51.5 + (i % 7) * 0.0002, -0.1 + (i % 5) * 0.0003)
        self.assertEqual(len(places), 1)

    def test_the_places_kept_for_one_address_are_bounded(self):
        places = []
        for i in range(m.MAX_PLACES + 20):
            m.add_place(places, 10 + i * STEP, 0)
        self.assertEqual(len(places), m.MAX_PLACES)

    def test_the_spread_is_the_greatest_distance_between_any_two(self):
        self.assertEqual(m.spread_m([]), 0.0)
        self.assertEqual(m.spread_m([(51.5, -0.1)]), 0.0)
        self.assertAlmostEqual(m.spread_m([(51.5, -0.1), (51.5 + 3 * STEP, -0.1), (51.5 + STEP, -0.1)]), 1334, delta=3)

    def test_a_fix_is_usable_only_when_real_and_not_too_vague(self):
        self.assertTrue(m.usable_fix(fix()))
        self.assertTrue(m.usable_fix(fix(epx=20.0, epy=99.0)))
        self.assertFalse(m.usable_fix(fix(epx=20.0, epy=250.0)))
        self.assertFalse(m.usable_fix(None))
        self.assertFalse(m.usable_fix({"class": "TPV", "mode": 1}))
        self.assertFalse(m.usable_fix({"class": "TPV", "mode": 3, "lat": 51.5}))


class TravelTests(unittest.TestCase):
    def test_the_thresholds_are_the_ones_asked_for(self):
        self.assertEqual((m.MIN_PLACES, m.PLACE_METRES, m.MIN_SECONDS), (3, 300.0, 600))

    def test_three_places_over_ten_minutes_meets_the_rule_once(self):
        travel, met = walked(places=3, minutes=10)
        self.assertEqual([t["addr"] for t in met], [PUBLIC])
        self.assertEqual([t["addr"] for t in travel.flagged()], [PUBLIC])

    def test_two_places_is_not_enough_however_long(self):
        travel = m.Travel()
        travel.add(snap(NOON), fix(51.5))
        travel.add(snap(NOON + 7200 - 1), fix(51.5 + STEP))
        self.assertEqual(travel.flagged(), [])

    def test_three_places_in_under_ten_minutes_is_not_enough(self):
        travel, met = walked(places=3, minutes=9.9)
        self.assertEqual((met, travel.flagged()), ([], []))

    def test_it_is_flagged_once_the_time_is_reached_even_with_no_new_place(self):
        travel, _ = walked(places=3, minutes=5)
        met = travel.add(snap(NOON + 601), fix(51.5 + 2 * STEP))
        self.assertEqual(len(met), 1)

    def test_places_closer_than_300_m_do_not_count(self):
        travel = m.Travel()
        for i in range(30):
            travel.add(snap(NOON + i * 60), fix(51.5 + i * 0.0002))        # 22 m a minute: 645 m in all
        self.assertEqual(travel.places(PUBLIC), 3)
        travel = m.Travel()
        for i in range(30):
            travel.add(snap(NOON + i * 60), fix(51.5 + (i % 3) * 0.001))   # back and forth within 222 m
        self.assertEqual(travel.places(PUBLIC), 1)
        self.assertEqual(travel.flagged(), [])

    def test_nothing_is_noted_without_a_fix_or_with_a_vague_one(self):
        travel = m.Travel()
        for tpv in (None, {"class": "TPV", "mode": 1}, fix(epx=400.0)):
            self.assertEqual(travel.add(snap(NOON), tpv), [])
        self.assertEqual(travel.tallies, {})

    def test_a_private_address_is_never_tallied(self):
        travel = m.Travel()
        for i in range(5):
            travel.add(snap(NOON + i * 300, PRIVATE, "private (changes)"), fix(51.5 + i * STEP))
        self.assertEqual(travel.tallies, {})

    def test_a_device_not_heard_in_the_last_few_seconds_is_not_given_this_position(self):
        travel = m.Travel()
        travel.add(snap(NOON), fix(51.5))
        travel.add(snap(NOON + 400, last=NOON), fix(51.5 + STEP))       # still listed, but heard streets ago
        self.assertEqual(travel.places(PUBLIC), 1)
        self.assertEqual(travel.tallies[PUBLIC]["last"], NOON)

    def test_the_strongest_signal_and_the_latest_name_are_kept(self):
        travel, _ = walked()
        self.assertEqual(travel.tallies[PUBLIC]["best"], -45)
        self.assertEqual(travel.tallies[PUBLIC]["name"], "Kitchen Scale")

    def test_the_tally_carries_on_across_scan_restarts(self):
        # each restart of the scan begins a fresh snapshot list; the tally is not part of the scan
        travel = m.Travel()
        travel.add(snap(NOON), fix(51.5))
        travel.add(snap(NOON + 590), fix(51.5 + STEP))                  # end of the first ten minutes
        met = travel.add(snap(NOON + 640), fix(51.5 + 2 * STEP))        # first snapshot of the next scan
        self.assertEqual(len(met), 1)
        self.assertEqual(travel.tallies[PUBLIC]["first"], NOON)

    def test_an_address_silent_for_hours_starts_again_from_nothing(self):
        travel, _ = walked()
        travel.add(snap(NOON + 23 * 60 + m.TALLY_KEEP + 1), fix(60.0))
        self.assertEqual(travel.places(PUBLIC), 1)
        self.assertEqual(travel.flagged(), [])

    def test_one_marked_as_mine_is_not_flagged_and_is_again_when_unmarked(self):
        travel, _ = walked()
        mem = m.Memory()
        mem.see(PUBLIC, "public", when=NOON)
        mem.set_mine(PUBLIC)
        self.assertEqual(travel.flagged(mem), [])
        self.assertEqual(travel.to_tell(mem), [])
        mem.set_mine(PUBLIC, False)
        self.assertEqual(len(travel.flagged(mem)), 1)

    def test_each_flagged_address_is_handed_out_for_a_notification_once(self):
        travel, _ = walked()
        self.assertEqual(len(travel.to_tell()), 1)
        travel.add(snap(NOON + 3000), fix(51.6))
        self.assertEqual(travel.to_tell(), [])
        self.assertEqual(len(travel.flagged()), 1)

    def test_the_number_of_addresses_tallied_is_bounded(self):
        travel = m.Travel()
        with mock.patch.object(m, "MAX_TALLIED", 5):
            for i in range(9):
                travel.add(snap(NOON + i, "00:00:00:00:00:%02x" % i), fix())
        self.assertEqual(sorted(travel.tallies), ["00:00:00:00:00:%02x" % i for i in range(4, 9)])

    def test_positions_are_never_written_to_the_memory_file(self):
        travel, _ = walked()
        mem = m.Memory()
        for i in range(4):
            mem.see_snapshot(snap(NOON + i * 400))
        text = mem.to_json()
        for word in ("lat", "lon", "places", "51.5"):
            self.assertNotIn(word, text)


class SentenceTests(unittest.TestCase):
    def setUp(self):
        self.travel, _ = walked()
        self.tally = self.travel.tallies[PUBLIC]

    def every_sentence(self):
        nameless = dict(self.tally, name="", maker="", best=None)
        out = [m.measured_text(self.tally), m.flag_line(self.tally), m.flag_line(nameless), m.CAVEAT,
               m.WHY_NOT_REMEMBERED, m.banner_text([self.tally]), m.banner_text([self.tally] * 6),
               m.known_tip("public"), m.known_tip("private (changes)"), m.known_tip("random"),
               dict(notify.SOURCES)[m.NOTIFY_SOURCE]]
        out += list(m.describe(self.tally)[1:3]) + list(m.describe(nameless)[1:3])
        for rail_on in (False, True):
            for tpv in (None, fix(), fix(epx=900.0)):
                for scanning in (False, True):
                    for flagged in (0, 1, 3):
                        out.append(m.places_note(rail_on, tpv, scanning, 7, flagged))
        for kind in ("public", "private (changes)"):
            for mine in (False, True):
                out.append(m.pick_line(PUBLIC, kind, "Scale", mine))
        for entry in (None, {"first": 0, "last": NOON, "days": 3, "day": "x", "mine": True}):
            out.append(m.known_text("public", entry, NOON, NOON, 4))
        return out

    def test_it_says_exactly_what_was_measured(self):
        self.assertEqual(m.measured_text(self.tally), "Heard at 4 places up to 1.3 km apart over 23 min")
        self.assertEqual(m.flag_line(self.tally), "Kitchen Scale (Acme, 11:22:33:44:55:66): heard at 4 places up to "
                                                  "1.3 km apart over 23 min, strongest -45 dBm.")

    def test_short_distances_are_in_metres_and_long_ones_in_kilometres(self):
        self.assertEqual(m.distance_text(444.7), "440 m")
        self.assertEqual(m.distance_text(999.0), "1000 m")
        self.assertEqual(m.distance_text(1234.0), "1.2 km")
        self.assertEqual(m.minutes_text(23 * 60 + 59), "23 min")

    def test_a_device_with_no_name_or_maker_is_still_described_by_its_address(self):
        line = m.flag_line(dict(self.tally, name="", maker="", best=None))
        self.assertTrue(line.startswith("No name (11:22:33:44:55:66): heard at 4 places"))
        self.assertIn("signal strength not given", line)

    def test_no_sentence_claims_what_was_not_measured(self):
        for text in self.every_sentence():
            low = text.lower()
            for word in FORBIDDEN:
                self.assertNotIn(word, low, text)

    def test_no_sentence_gives_a_direction_distance_to_or_position_of_the_device(self):
        for text in self.every_sentence():
            low = text.lower()
            for word in ("metres away", "m away", "direction", "located", "where it is", "same device", "51.5"):
                self.assertNotIn(word, low, text)

    def test_the_banner_says_your_own_things_show_up_and_changing_addresses_cannot_be_picked_out(self):
        text = m.banner_text([self.tally])
        self.assertIn("Things you carry yourself (a watch, earbuds, a phone) show up this way", text)
        self.assertIn("changes its address cannot be picked out", text)
        self.assertIn("Mark as mine", text)
        self.assertEqual(m.banner_text([]), "")

    def test_a_long_banner_is_cut_to_three_addresses_and_says_how_many_more(self):
        text = m.banner_text([self.tally] * 5)
        self.assertEqual(text.count("heard at 4 places"), 3)
        self.assertIn("And 2 more addresses heard the same way.", text)
        self.assertIn("And 1 more address heard the same way.", m.banner_text([self.tally] * 4))

    def test_the_notification_names_what_was_measured_and_fits_in_full(self):
        long = dict(self.tally, name="A very long advertised device name of 40ch", maker="Some Long Maker Name Ltd", best=-100)
        for tally in (self.tally, long):
            source, title, body, level = m.describe(tally)
            self.assertEqual((source, level), ("btplaces", "warn"))
            self.assertLessEqual(len(title), 40)
            self.assertLessEqual(len(body), 160, body)
            self.assertIn("heard at 4 places up to 1.3 km apart over 23 min", body)
            self.assertIn("11:22:33:44:55:66", body)
            self.assertIn("Things you carry do this too", body)
        self.assertIn("strongest -45 dBm", m.describe(self.tally)[2])

    def test_the_notification_can_be_switched_off_in_settings_and_starts_on(self):
        self.assertIn(m.NOTIFY_SOURCE, dict(notify.SOURCES))
        self.assertTrue(notify.DEFAULTS[m.NOTIFY_SOURCE])
        centre = notify.Center(notify.config({"notifications": {m.NOTIFY_SOURCE: False}}))
        self.assertIsNone(centre.add(*m.describe(self.tally)))
        self.assertIsNotNone(notify.Center(notify.config({})).add(*m.describe(self.tally)))

    def test_without_the_gps_rail_it_says_what_it_needs_and_that_it_will_not_switch_it(self):
        text = m.places_note(False, None, True, 0, 0)
        self.assertIn("needs the GPS rail on and a fix", text)
        self.assertIn("never switched on for you", text)

    def test_the_line_follows_the_fix_and_the_tally(self):
        self.assertIn("while a scan runs", m.places_note(True, fix(), False, 0, 0))
        self.assertIn("Waiting for a GPS fix", m.places_note(True, None, True, 0, 0))
        self.assertIn("Waiting for a GPS fix", m.places_note(True, {"mode": 1}, True, 0, 0))
        self.assertIn("too vague", m.places_note(True, fix(epx=500.0), True, 0, 0))
        self.assertIn("Noting where 1 fixed address is heard", m.places_note(True, fix(), True, 1, 0))
        self.assertIn("Noting where 7 fixed addresses are heard", m.places_note(True, fix(), True, 7, 0))
        self.assertTrue(m.places_note(True, fix(), True, 7, 2).startswith("2 addresses heard at 3 or more places 300 m apart"))


if __name__ == "__main__":
    unittest.main()
