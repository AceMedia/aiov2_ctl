import csv
import io
import json
import os
import stat
import tempfile
import unittest

from ops_gui import notify, survey as sv

SNAP = {
    "t": 100.0, "channel": 6,
    "aps": [{"bssid": "aa:bb:cc:00:11:22", "ssid": "HomeNet", "channel": 6, "security": "WPA2", "rssi": -48.2, "beacons": 9,
             "first": 90.0, "last": 99.0},
            {"bssid": "aa:bb:cc:00:11:33", "ssid": "", "channel": 36, "security": "Open", "rssi": -80.0, "beacons": 2,
             "first": 95.0, "last": 40.0}],
    "stations": [{"mac": "02:11:22:33:44:55", "bssid": "aa:bb:cc:00:11:22", "rssi": -60.0, "frames": 12, "last": 98.0}],
    "probes": [{"mac": "02:aa:aa:aa:aa:aa", "ssids": ["MyPhone"], "count": 3, "rssi": -70.0},
               {"mac": "02:bb:bb:bb:bb:bb", "ssids": [], "count": 1, "rssi": -75.0}],
    "deauth": [{"bssid": "aa:bb:cc:00:11:22", "frames": 14, "total": 30}],
    "channels": {"1": {"frames": 10, "bytes": 1}, "6": {"frames": 40, "bytes": 1}, "36": {"frames": 0, "bytes": 0}},
}
FIX = {"mode": 3, "lat": 51.5, "lon": -0.12, "alt": 20.0, "epx": 4.0, "epy": 6.0}


class ParseTests(unittest.TestCase):
    def test_snapshot(self):
        self.assertEqual(sv.parse_snapshot(json.dumps(SNAP))["channel"], 6)
        for bad in ("", "nope", "[]", '{"t": 1}', '{"aps": []}', None, '{"t": 1, "aps": [], "stations": 5}',
                    '{"t": 1, "aps": [], "channels": []}', '{"t": "x", "aps": []}'):
            self.assertIsNone(sv.parse_snapshot(bad))

    def test_command_matches_the_sudoers_rule(self):
        self.assertEqual(" ".join(sv.survey_args("2g", False)[3:]),
                         "--wifi-survey --iface wlan1 --band 2g --seconds 3600 --json")
        self.assertEqual(" ".join(sv.survey_args("all", True)[3:]),
                         "--wifi-survey --iface wlan1 --band all --seconds 3600 --pcap --json")
        self.assertEqual(sv.survey_args("6g")[7], "all")


class RowTests(unittest.TestCase):
    def test_rows(self):
        aps = sv.ap_rows(SNAP)
        self.assertEqual((aps[0]["ssid"], aps[0]["signal"], aps[0]["seen"]), ("HomeNet", "-48 dBm", "1s"))
        self.assertEqual((aps[1]["ssid"], aps[1]["seen"]), ("<hidden>", "60s"))
        devices = sv.station_rows(SNAP)
        self.assertEqual(devices[0]["network"], "HomeNet")
        self.assertEqual([d["mac"] for d in devices], ["02:11:22:33:44:55", "02:aa:aa:aa:aa:aa", "02:bb:bb:bb:bb:bb"])
        self.assertEqual(devices[1]["network"], "probing for MyPhone")
        self.assertEqual(devices[2]["network"], "probing (no network named)")
        self.assertEqual([p["looking"] for p in sv.probe_rows(SNAP)], ["MyPhone", "(any network)"])

    def test_channel_bars(self):
        self.assertEqual(sv.channel_shares(SNAP), [(1, 0.25), (6, 1.0), (36, 0.0)])
        self.assertEqual(sv.channel_shares({"channels": {}}), [])

    def test_summary(self):
        self.assertIn("2 access points, 3 devices", sv.summary(SNAP))
        self.assertIn("1 deauthentication burst now", sv.summary(SNAP))
        self.assertTrue(sv.summary(dict(SNAP, deauth=[])).endswith("devices."))


class AlertTests(unittest.TestCase):
    def test_one_alert_a_minute(self):
        seen = {}
        first = sv.deauth_alerts(SNAP, seen, 100.0)
        self.assertEqual(len(first), 1)
        self.assertIn("HomeNet", first[0][1])
        self.assertEqual(sv.deauth_alerts(SNAP, seen, 130.0), [])
        self.assertEqual(len(sv.deauth_alerts(SNAP, seen, 161.0)), 1)

    def test_the_source_exists_and_is_off_by_default(self):
        self.assertIn("survey", dict(notify.SOURCES))
        self.assertFalse(notify.config({})["survey"])


class RowFormattingTests(unittest.TestCase):
    def test_dbm_text(self):
        self.assertEqual(sv.dbm_text(-50.4), "-50 dBm")
        self.assertEqual(sv.dbm_text(0.0), "0 dBm")
        self.assertEqual(sv.dbm_text(None), "-")

    def test_rows_do_not_crash_on_an_explicit_none_rssi(self):
        snap = {"t": 100.0,
                "aps": [{"bssid": "aa:bb:cc:00:11:22", "ssid": "Home", "channel": 6, "security": "WPA2",
                        "rssi": None, "last": 99.0}],
                "stations": [{"mac": "02:11", "bssid": "aa:bb:cc:00:11:22", "rssi": None, "frames": 1, "last": 99.0}],
                "probes": [{"mac": "02:22", "ssids": [], "count": 1, "rssi": None}]}
        self.assertEqual(sv.ap_rows(snap)[0]["signal"], "-")
        self.assertEqual(sv.station_rows(snap)[0]["signal"], "-")
        self.assertEqual(sv.station_rows(snap)[1]["signal"], "-")


class WigleTests(unittest.TestCase):
    def test_needs_a_real_fix(self):
        log = sv.WigleLog()
        for bad in (None, {}, {"mode": 1, "lat": 1, "lon": 1}, {"mode": 3, "lat": None, "lon": 1}):
            self.assertEqual(log.add(SNAP, bad, 1.0), 0)
        self.assertEqual(log.nets, {})
        self.assertEqual(log.add(SNAP, FIX, 1.0), 1)       # the second network was last heard a minute before
        self.assertEqual(log.add(SNAP, FIX, 2.0), 0)

    def test_best_signal_position_wins_and_first_seen_stays(self):
        log = sv.WigleLog()
        log.add(SNAP, FIX, 10.0)
        stronger = dict(SNAP, aps=[dict(SNAP["aps"][0], rssi=-30.0)])
        log.add(stronger, dict(FIX, lat=52.0), 20.0)
        net = log.nets["aa:bb:cc:00:11:22"]
        self.assertEqual((net["lat"], net["rssi"], net["first"]), (52.0, -30.0, 10.0))
        weaker = dict(SNAP, aps=[dict(SNAP["aps"][0], rssi=-70.0)])
        log.add(weaker, dict(FIX, lat=53.0), 30.0)
        self.assertEqual(log.nets["aa:bb:cc:00:11:22"]["lat"], 52.0)

    def test_csv_is_wigle_shaped(self):
        log = sv.WigleLog()
        snap = dict(SNAP, aps=[dict(SNAP["aps"][0], ssid='Cafe, "Best"')])
        log.add(snap, FIX, 1795541000.0)
        lines = log.csv_text().split("\n")
        self.assertTrue(lines[0].startswith("WigleWifi-1.4,"))
        rows = list(csv.reader(io.StringIO("\n".join(lines[1:]))))
        self.assertEqual(rows[0], list(sv.WIGLE_COLUMNS))
        row = rows[1]
        self.assertEqual((row[0], row[1], row[2], row[4], row[5], row[10]),
                         ("AA:BB:CC:00:11:22", 'Cafe, "Best"', "[WPA2-PSK-CCMP][ESS]", "6", "-48", "WIFI"))
        self.assertEqual((row[6], row[7]), ("51.500000", "-0.120000"))
        self.assertRegex(row[3], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")

    def test_auth_words(self):
        self.assertEqual(sv.wigle_auth("Open"), "[ESS]")
        self.assertEqual(sv.wigle_auth("WPA3"), "[WPA3-SAE-CCMP][ESS]")
        self.assertEqual(sv.wigle_auth("something new"), "[ESS]")

    def test_the_file_is_private_and_never_overwritten(self):
        log = sv.WigleLog()
        log.add(SNAP, FIX, 1.0)
        with tempfile.TemporaryDirectory() as d:
            path = log.save(d, now=1795541000)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertTrue(path.endswith(".wigle.csv"))
            # saving again in the same second (Save, then Stop, say) must not raise or clobber the first file:
            # it gets its own name instead
            before = open(path).read()
            path2 = log.save(d, now=1795541000)
            self.assertNotEqual(path, path2)
            self.assertEqual(open(path).read(), before)
            self.assertEqual(stat.S_IMODE(os.stat(path2).st_mode), 0o600)


class SenseThisNetworkTests(unittest.TestCase):
    """The "Sense this network" button under the access point table."""

    def rows(self):
        return sv.ap_rows(SNAP)

    def test_a_picked_access_point_gives_sense_its_address_and_channel(self):
        target, words = sv.sense_offer(self.rows()[0], True, {})
        self.assertEqual(target, ("aa:bb:cc:00:11:22", 6))
        self.assertIn("HomeNet", words)
        self.assertIn("channel 6", words)
        self.assertIn("Stops the survey", words)
        self.assertIn("Nothing is sent", words)

    def test_a_hidden_network_on_5_ghz_can_be_sensed_too(self):
        target, words = sv.sense_offer(self.rows()[1], True, {})
        self.assertEqual(target, ("aa:bb:cc:00:11:33", 36))
        self.assertIn("aa:bb:cc:00:11:33", words)               # no name to show, so it is named by its address

    def test_nothing_picked_offers_nothing_and_says_nothing(self):
        self.assertEqual(sv.sense_offer(None, True, {}), (None, ""))

    def test_a_row_with_no_channel_cannot_be_sensed(self):
        snap = dict(SNAP, aps=[{"bssid": "aa:bb:cc:00:11:22", "ssid": "HomeNet", "channel": None}])
        target, words = sv.sense_offer(sv.ap_rows(snap)[0], True, {})
        self.assertIsNone(target)
        self.assertIn("not known yet", words)

    def test_a_row_with_no_address_cannot_be_sensed(self):
        for bssid in ("", "nonsense", "aa:bb:cc:00:11"):
            target, words = sv.sense_offer({"ssid": "HomeNet", "bssid": bssid, "channel": "6"}, True, {})
            self.assertIsNone(target, bssid)
            self.assertIn("no address", words)

    def test_a_channel_the_listener_is_not_allowed_on_cannot_be_sensed(self):
        target, words = sv.sense_offer({"ssid": "Radar", "bssid": "aa:bb:cc:00:11:22", "channel": "52"}, True, {})
        self.assertIsNone(target)
        self.assertIn("cannot listen on channel 52", words)

    def test_it_is_not_offered_when_the_radio_is_not_there(self):
        target, words = sv.sense_offer(self.rows()[0], False, {})
        self.assertIsNone(target)
        self.assertIn("not there", words)
        self.assertIn("Switch the USB rail on", words)          # the user is told to: it is never switched on for them

    def test_it_is_not_offered_when_the_radio_is_connected_to_a_network(self):
        target, words = sv.sense_offer(self.rows()[0], True, {"wlan1": -50.0})
        self.assertIsNone(target)
        self.assertIn("wlan1 is connected", words)

    def test_another_radio_being_connected_does_not_stop_it(self):
        self.assertIsNotNone(sv.sense_offer(self.rows()[0], True, {"wlan0": -50.0})[0])

    def test_an_upper_case_address_is_handed_over_in_lower_case(self):
        target, _ = sv.sense_offer({"ssid": "X", "bssid": "AA:BB:CC:00:11:22", "channel": "11"}, True, {})
        self.assertEqual(target, ("aa:bb:cc:00:11:22", 11))

    def test_the_picked_row_is_found_again_after_the_table_reorders(self):
        rows = self.rows()
        self.assertEqual(sv.row_of(rows, "aa:bb:cc:00:11:33"), 1)
        self.assertEqual(sv.row_of(list(reversed(rows)), "aa:bb:cc:00:11:33"), 0)

    def test_a_picked_row_that_is_gone_or_nothing_picked_is_not_found(self):
        self.assertEqual(sv.row_of(self.rows(), "aa:bb:cc:99:99:99"), -1)
        self.assertEqual(sv.row_of(self.rows(), ""), -1)
        self.assertEqual(sv.row_of([], "aa:bb:cc:00:11:22"), -1)

    def test_the_offer_makes_no_claim_about_people_or_places(self):
        _, words = sv.sense_offer(self.rows()[0], True, {})
        for word in ("person", "people", "where", "direction", "locat", "track"):
            self.assertNotIn(word, words.lower())


class HonestyTests(unittest.TestCase):
    def test_nothing_that_transmits(self):
        import inspect
        src = inspect.getsource(sv)
        for word in ("deauth(", "inject", "sendto", "beaconspam", "evilportal", "--yes"):
            self.assertNotIn(word, src)


class HeardHereTests(unittest.TestCase):
    """A position is only given to what was heard at that position."""
    FIX = {"mode": 3, "lat": 53.1, "lon": -1.2, "alt": 200.0}

    def test_a_network_heard_minutes_ago_is_not_stamped_with_where_we_are_now(self):
        from ops_gui import survey
        log = survey.WigleLog()
        snap = {"t": 1000.0, "aps": [{"bssid": "aa:bb:cc:00:00:01", "rssi": -60, "last": 999.0},
                                     {"bssid": "aa:bb:cc:00:00:02", "rssi": -50, "last": 700.0}]}
        self.assertEqual(log.add(snap, self.FIX, now=1000.0), 1)
        self.assertEqual(list(log.nets), ["aa:bb:cc:00:00:01"])

    def test_it_is_logged_when_it_is_heard_again(self):
        from ops_gui import survey
        log = survey.WigleLog()
        old = {"bssid": "aa:bb:cc:00:00:02", "rssi": -50, "last": 700.0}
        log.add({"t": 1000.0, "aps": [old]}, self.FIX, now=1000.0)
        self.assertEqual(log.nets, {})
        log.add({"t": 1100.0, "aps": [dict(old, last=1099.5)]}, self.FIX, now=1100.0)
        self.assertIn("aa:bb:cc:00:00:02", log.nets)

    def test_a_stronger_old_reading_does_not_move_a_logged_network(self):
        from ops_gui import survey
        log = survey.WigleLog()
        log.add({"t": 1000.0, "aps": [{"bssid": "aa:bb:cc:00:00:01", "rssi": -70, "last": 1000.0}]}, self.FIX, now=1000.0)
        there = dict(self.FIX, lat=54.0)
        log.add({"t": 1200.0, "aps": [{"bssid": "aa:bb:cc:00:00:01", "rssi": -40, "last": 1050.0}]}, there, now=1200.0)
        self.assertEqual(log.nets["aa:bb:cc:00:00:01"]["lat"], 53.1)

    def test_with_no_time_to_go_by_it_counts_as_heard_now(self):
        from ops_gui import survey
        self.assertTrue(survey.heard_now({}, 1000.0))
        self.assertTrue(survey.heard_now({"last": 990.0}, None))
        self.assertTrue(survey.heard_now({"last": 1000.0 - survey.HEARD_NOW}, 1000.0))
        self.assertFalse(survey.heard_now({"last": 1000.0 - survey.HEARD_NOW - 1}, 1000.0))

    def test_bluetooth_follows_the_same_rule(self):
        from ops_gui import bt_panel
        log = bt_panel.BtLog()
        snap = {"t": 1000.0, "devices": [{"addr": "11:22:33:44:55:66", "rssi": -60, "last": 999.0},
                                         {"addr": "11:22:33:44:55:77", "rssi": -50, "last": 600.0}]}
        self.assertEqual(log.add(snap, self.FIX), 1)
        self.assertEqual(list(log.nets), ["11:22:33:44:55:66"])


if __name__ == "__main__":
    unittest.main()
