import csv
import io
import json
import os
import stat
import tempfile
import unittest

from ops_gui import bt_panel as bt, position as p, survey

FIX = {"class": "TPV", "mode": 3, "lat": 51.5, "lon": -0.12, "alt": 20.0, "epx": 4.0, "epy": 6.0}
SNAP = {"t": 1000.0, "devices": [
    {"addr": "11:22:33:44:55:66", "name": "Kitchen Scale", "rssi": -60, "kind": "public"},
    {"addr": "46:05:04:03:02:01", "name": "", "rssi": -75, "kind": "private (changes)"},
    {"addr": "c6:05:04:03:02:01", "name": "", "rssi": None, "kind": "random (fixed)"}]}


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


class GpsdLineTests(unittest.TestCase):
    def test_the_newest_position_report_is_picked_out(self):
        lines = ['{"class":"VERSION"}', json.dumps(dict(FIX, lat=1.0)), "junk", '{"class":"SKY"}', json.dumps(FIX)]
        self.assertEqual(p.tpv_from_lines(lines)["lat"], 51.5)

    def test_nothing_useful_is_none(self):
        for lines in ([], None, ["junk", '{"class":"SKY"}', "[1,2]"]):
            self.assertIsNone(p.tpv_from_lines(lines))

    def test_what_counts_as_a_fix(self):
        self.assertTrue(p.good_fix(FIX))
        for tpv in (None, {}, dict(FIX, mode=1), dict(FIX, mode=None), dict(FIX, lat=None), {"mode": 3}):
            self.assertFalse(p.good_fix(tpv))

    def test_the_rail_is_read_from_the_apps_own_status(self):
        self.assertTrue(p.gps_rail_on({"rails": {"GPS": "on"}}))
        for snap in (None, {}, {"rails": {"GPS": "off"}}, {"rails": None}, {"rails": {"LORA": "on"}}):
            self.assertFalse(p.gps_rail_on(snap))


class PositionTests(unittest.TestCase):
    def test_a_reading_is_handed_out_only_while_fresh(self):
        clock = Clock()
        pos = p.Position(query=lambda: [json.dumps(FIX)], clock=clock)
        self.assertIsNone(pos.current())
        self.assertTrue(pos.poll(background=False))
        self.assertEqual(pos.current()["lat"], 51.5)
        clock.t += p.MAX_AGE + 1
        self.assertIsNone(pos.current())                                   # stale: not where we are any more

    def test_gpsd_is_not_asked_more_often_than_it_should_be(self):
        clock, calls = Clock(), []
        pos = p.Position(query=lambda: calls.append(1) or [json.dumps(FIX)], clock=clock)
        self.assertTrue(pos.poll(background=False))
        self.assertFalse(pos.poll(background=False))
        clock.t += p.POLL_SECONDS
        self.assertTrue(pos.poll(background=False))
        self.assertEqual(len(calls), 2)

    def test_no_answer_keeps_the_old_reading_until_it_goes_stale(self):
        clock, answers = Clock(), [[json.dumps(FIX)], []]
        pos = p.Position(query=lambda: answers.pop(0), clock=clock)
        pos.poll(background=False)
        clock.t += p.POLL_SECONDS
        pos.poll(background=False)
        self.assertEqual(pos.current()["lat"], 51.5)
        self.assertFalse(pos.busy)

    def test_a_query_that_blows_up_does_not_leave_it_stuck(self):
        def boom():
            raise OSError("no gpsd")
        pos = p.Position(query=boom, clock=Clock())
        with self.assertRaises(OSError):
            pos.poll(background=False)
        self.assertFalse(pos.busy)

    def test_nothing_is_asked_while_the_gps_rail_is_off(self):
        class Win:
            snap = {"rails": {"GPS": "off"}}
            position = None
        win = Win()
        self.assertIsNone(p.current(win))
        self.assertIsNone(win.position)                                    # not even created


class BluetoothLogTests(unittest.TestCase):
    def test_nothing_is_logged_without_a_fix(self):
        log = bt.BtLog()
        for tpv in (None, {}, dict(FIX, mode=1)):
            self.assertEqual(log.add(SNAP, tpv), 0)
        self.assertEqual(log.nets, {})

    def test_devices_are_logged_where_they_were_strongest(self):
        log = bt.BtLog()
        self.assertEqual(log.add(SNAP, FIX), 2)                            # the one with no signal reading is left out
        closer = {"t": 1010.0, "devices": [dict(SNAP["devices"][0], rssi=-40)]}
        self.assertEqual(log.add(closer, dict(FIX, lat=51.6)), 0)
        self.assertEqual(log.nets["11:22:33:44:55:66"]["lat"], 51.6)
        weaker = {"t": 1020.0, "devices": [dict(SNAP["devices"][0], rssi=-90)]}
        log.add(weaker, dict(FIX, lat=52.0))
        self.assertEqual(log.nets["11:22:33:44:55:66"]["lat"], 51.6)
        self.assertEqual(log.nets["11:22:33:44:55:66"]["first"], 1000.0)

    def test_the_file_is_wigle_csv_with_ble_rows(self):
        log = bt.BtLog()
        log.add(SNAP, FIX)
        text = log.csv_text()
        self.assertTrue(text.startswith("WigleWifi-1.4"))
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(tuple(rows[1]), survey.WIGLE_COLUMNS)
        first = dict(zip(rows[1], rows[2]))
        self.assertEqual((first["MAC"], first["SSID"], first["Type"], first["RSSI"]),
                         ("11:22:33:44:55:66".upper(), "Kitchen Scale", "BLE", "-60"))
        self.assertEqual((first["CurrentLatitude"], first["CurrentLongitude"]), ("51.500000", "-0.120000"))
        self.assertEqual({r[-1] for r in rows[2:]}, {"BLE"})

    def test_the_wifi_log_still_writes_wifi_rows(self):
        log = survey.WigleLog()
        log.add({"aps": [{"bssid": "aa:bb:cc:00:11:22", "ssid": "HomeNet", "rssi": -50, "security": "WPA2", "channel": 6}]},
                FIX, now=5.0)
        self.assertTrue(log.csv_text().strip().endswith("WIFI"))

    def test_it_saves_privately_under_its_own_name(self):
        log = bt.BtLog()
        log.add(SNAP, FIX)
        with tempfile.TemporaryDirectory() as d:
            path = log.save(d, now=0)
            self.assertTrue(os.path.basename(path).startswith("bluetooth-"))
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_the_line_under_the_table_follows_the_rail_the_tick_and_the_fix(self):
        self.assertIn("Switch the GPS rail on", bt.log_note(False, False, None, 0))
        self.assertIn("tick Log with position", bt.log_note(True, False, None, 0))
        self.assertIn("Waiting for a GPS fix", bt.log_note(True, True, None, 0))
        self.assertIn("Waiting for a GPS fix", bt.log_note(True, True, dict(FIX, mode=1), 3))
        self.assertEqual(bt.log_note(True, True, FIX, 1), "Logging: 1 address with a position.")
        self.assertEqual(bt.log_note(True, True, FIX, 4), "Logging: 4 addresses with a position.")


class OneGpsdAskerTests(unittest.TestCase):
    """Everything in the app that wants gpsd shares one asking."""

    def setUp(self):
        self.now, self.asked = 100.0, 0

        def query():
            self.asked += 1
            return ['{"class":"TPV","mode":3,"n":%d}' % self.asked]
        from ops_gui import position
        self.position = position
        self.src = position.GpsdLines(query, clock=lambda: self.now)

    def test_two_askers_close_together_cost_one_asking(self):
        first = self.src.get(4.0)
        self.now += 2.0
        self.assertEqual(self.src.get(4.0), first)
        self.assertEqual(self.asked, 1)

    def test_an_old_answer_is_asked_for_again(self):
        self.src.get(4.0)
        self.now += 4.5
        self.assertIn('"n":2', self.src.get(4.0)[0])
        self.assertEqual(self.asked, 2)

    def test_each_asker_says_how_old_an_answer_it_will_take(self):
        self.src.get(4.0)
        self.now += 3.0
        self.src.get(10.0)                                    # Home is happy with an older one
        self.assertEqual(self.asked, 1)
        self.src.get(2.0)                                     # the field log is not
        self.assertEqual(self.asked, 2)

    def test_no_answer_from_gpsd_is_an_empty_list_not_an_error(self):
        src = self.position.GpsdLines(lambda: None, clock=lambda: self.now)
        self.assertEqual(src.get(4.0), [])

    def test_askers_at_the_same_moment_wait_for_one_answer(self):
        import threading
        import time as real_time
        calls = []

        def slow():
            calls.append(1)
            real_time.sleep(0.2)
            return ["x"]
        src = self.position.GpsdLines(slow)
        threads = [threading.Thread(target=src.get) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
