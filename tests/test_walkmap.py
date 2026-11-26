"""The map of a walk: the track, what the saved logs heard, where it all goes on the screen, and the exports."""
import ast
import json
import os
import stat
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from ops_gui import bt_panel, everything as ev, reports, survey, walkmap as wm

T0 = 1795000000.0
GPX = "{http://www.topografix.com/GPX/1/1}"


def fix(at, lat, lon, alt=None):
    rec = {"k": "sample", "t": ev.stamp(T0 + at), "ts": T0 + at, "lat": lat, "lon": lon, "gps": {"mode": 3}}
    if alt is not None:
        rec["gps"]["alt"] = alt
    return rec


def row(kind="wifi", addr="aa:bb:cc:dd:ee:01", name="Cafe", rssi=-60.0, at=10, lat=53.1, lon=-1.2):
    return {"kind": kind, "addr": addr, "name": name, "rssi": rssi, "ts": T0 + at, "lat": lat, "lon": lon,
            "channel": "6", "auth": "[WPA2-PSK-CCMP][ESS]"}


def wifi_log(*heard):
    """A wardrive log as the survey page saves it: (bssid, name, rssi, seconds after T0, lat, lon)."""
    log = survey.WigleLog()
    for bssid, name, rssi, at, lat, lon in heard:
        log.add({"aps": [{"bssid": bssid, "ssid": name, "rssi": rssi, "security": "WPA2", "channel": 6}]},
                {"mode": 3, "lat": lat, "lon": lon}, now=T0 + at)
    return log


def bt_log(*heard):
    log = bt_panel.BtLog()
    for addr, name, rssi, at, lat, lon in heard:
        log.add({"t": T0 + at, "devices": [{"addr": addr, "name": name, "rssi": rssi}]},
                {"mode": 3, "lat": lat, "lon": lon})
    return log


class TrackTests(unittest.TestCase):
    def test_the_track_is_the_readings_that_had_a_fix_in_time_order(self):
        samples = [fix(10, 53.2, -1.3, 120.5), {"k": "sample", "ts": T0 + 5, "t": "x", "gps": {"mode": 1}}, fix(0, 53.1, -1.2)]
        self.assertEqual(wm.track_of(samples), [{"lat": 53.1, "lon": -1.2, "t": T0, "alt": None},
                                                {"lat": 53.2, "lon": -1.3, "t": T0 + 10, "alt": 120.5}])

    def test_a_damaged_reading_is_left_out_rather_than_guessed_at(self):
        bad = [None, "x", {"ts": T0, "lat": "53.1", "lon": -1.2}, {"ts": T0, "lat": 99.0, "lon": 0.0},
               {"ts": T0, "lat": float("nan"), "lon": 0.0}, {"ts": "now", "lat": 1.0, "lon": 1.0}, {"ts": T0, "lat": True, "lon": 1},
               {"ts": T0, "lat": 1.0}]
        self.assertEqual(wm.track_of(bad), [])
        self.assertEqual(wm.track_of(None), [])
        self.assertEqual(wm.track_of([dict(fix(0, 1.0, 2.0), gps="broken")])[0]["alt"], None)

    def test_you_were_here_is_the_newest_position_at_that_moment_and_never_old_news(self):
        track = wm.track_of([fix(0, 53.1, -1.2), fix(5, 53.2, -1.2), fix(100, 53.3, -1.2)])
        self.assertEqual(wm.here_index(track, T0 + 6, 12.5), 1)
        self.assertEqual(wm.here_index(track, T0 + 5, 12.5), 1)
        self.assertIsNone(wm.here_index(track, T0 + 50, 12.5))          # the fix was lost long before this
        self.assertIsNone(wm.here_index(track, T0 - 1, 12.5))
        self.assertIsNone(wm.here_index(track, None, 12.5))
        self.assertIsNone(wm.here_index([], T0, 12.5))


class LogTests(unittest.TestCase):
    """Reading the WiGLE CSVs the survey and the Bluetooth scan save."""

    def test_a_wardrive_log_reads_back_with_the_position_time_and_signal_it_was_saved_with(self):
        rows = wm.parse_wigle(wifi_log(("AA:BB:CC:DD:EE:01", "Cafe, the \"best\"", -61.4, 7, 53.123456, -1.234567)).csv_text())
        self.assertEqual(rows, [{"kind": "wifi", "addr": "aa:bb:cc:dd:ee:01", "name": "Cafe, the \"best\"", "rssi": -61.0,
                                 "ts": T0 + 7, "lat": 53.123456, "lon": -1.234567, "channel": "6",
                                 "auth": "[WPA2-PSK-CCMP][ESS]"}])

    def test_a_bluetooth_log_reads_back_as_bluetooth(self):
        rows = wm.parse_wigle(bt_log(("11:22:33:44:55:66", "Watch", -70, 3, 53.1, -1.2)).csv_text())
        self.assertEqual([(r["kind"], r["addr"], r["name"], r["ts"]) for r in rows],
                         [("bt", "11:22:33:44:55:66", "Watch", T0 + 3)])

    def test_rows_that_are_cut_short_or_make_no_sense_are_skipped(self):
        good = wifi_log(("aa:bb:cc:dd:ee:01", "Cafe", -60, 1, 53.1, -1.2)).csv_text()
        head = "\n".join(good.splitlines()[:2]) + "\n"
        when = "2026-11-17 12:00:00"
        bad = [f"aa:aa:aa:aa:aa:01,x,[ESS],{when},6,-60,53.1",                              # cut short by a flat battery
               f"aa:aa:aa:aa:aa:02,x,[ESS],{when},6,-60,,,0,10,WIFI",                       # no position
               f"aa:aa:aa:aa:aa:03,x,[ESS],{when},6,-60,95.0,-1.2,0,10,WIFI",               # not a place on Earth
               f"aa:aa:aa:aa:aa:04,x,[ESS],yesterday,6,-60,53.1,-1.2,0,10,WIFI",            # no time
               f"aa:aa:aa:aa:aa:05,x,[ESS],{when},6,loud,53.1,-1.2,0,10,WIFI",              # no signal
               f"aa:aa:aa:aa:aa:06,x,[ESS],{when},6,-60,nan,-1.2,0,10,WIFI",
               f"aa:aa:aa:aa:aa:07,x,[ESS],{when},6,-60,53.1,-1.2,0,10,GSM",                # a type we never write
               f",x,[ESS],{when},6,-60,53.1,-1.2,0,10,WIFI", "", "\x00\x00"]
        text = head + "\n".join(bad) + "\n" + good.splitlines()[2] + "\n"
        self.assertEqual([r["addr"] for r in wm.parse_wigle(text)], ["aa:bb:cc:dd:ee:01"])

    def test_a_file_that_is_not_a_log_at_all_gives_nothing(self):
        for text in (None, "", "hello", "MAC,SSID\n1,2\n", '{"k":"sample"}', "\x00" * 50, 'a,"unterminated\nb'):
            self.assertEqual(wm.parse_wigle(text), [])

    def test_only_what_was_first_heard_during_the_session_is_kept(self):
        rows = [row(addr="a", at=-1), row(addr="b", at=0), row(addr="c", at=30), row(addr="d", at=31)]
        self.assertEqual([r["addr"] for r in wm.in_span(rows, T0 + 0.4, T0 + 30)], ["b", "c"])
        self.assertEqual(wm.in_span([], T0, T0 + 30), [])

    def test_an_address_in_several_logs_is_one_mark_at_its_strongest(self):
        rows = [row(addr="a", rssi=-70, lat=53.1), row(addr="a", rssi=-50, lat=53.2), row(addr="a", rssi=-60, lat=53.3),
                row(kind="bt", addr="a", rssi=-80, at=2)]
        kept = wm.strongest(rows)
        self.assertEqual([(r["kind"], r["rssi"], r["lat"]) for r in kept], [("bt", -80, 53.1), ("wifi", -50, 53.2)])
        self.assertEqual([r["rssi"] for r in wm.strongest(rows, limit=1)], [-50])

    def test_the_logs_are_found_in_the_folder_the_scans_save_to_and_nothing_else_is_opened(self):
        with tempfile.TemporaryDirectory() as d:
            a = wifi_log(("aa:bb:cc:dd:ee:01", "Cafe", -60, 10, 53.1, -1.2)).save(d, now=T0 + 20)
            b = bt_log(("11:22:33:44:55:66", "Watch", -70, 12, 53.1, -1.2)).save(d, now=T0 + 20)
            c = wifi_log(("aa:bb:cc:dd:ee:01", "Cafe", -60, 10, 53.1, -1.2)).save(d, now=T0 + 20)   # the same second
            old = wifi_log(("aa:bb:cc:dd:ee:09", "Old", -60, -9000, 53.1, -1.2)).save(d, now=T0 - 8000)
            for other in ("notes.txt", "wardrive-latest.wigle.csv", "survey-20261117-120000.pcap"):
                open(os.path.join(d, other), "w").close()
            self.assertEqual(wm.log_files(d), sorted([a, b, c, old]))
            self.assertEqual(wm.log_files(d, since=T0), sorted([a, b, c]))    # saved before the session: not opened
            self.assertEqual(wm.log_files(os.path.join(d, "missing")), [])
        with mock.patch.dict(os.environ, {"HOME": "/home/someone"}):
            self.assertEqual(wm.log_dir(), "/home/someone/aiov2-captures")

    def test_what_was_heard_during_a_session_is_gathered_from_every_log_that_covers_it(self):
        with tempfile.TemporaryDirectory() as d:
            wifi_log(("aa:bb:cc:dd:ee:01", "Cafe", -70, 10, 53.1, -1.2),
                     ("aa:bb:cc:dd:ee:02", "Before", -50, -60, 53.1, -1.2)).save(d, now=T0 + 300)
            wifi_log(("aa:bb:cc:dd:ee:01", "Cafe", -55, 10, 53.2, -1.2),
                     ("aa:bb:cc:dd:ee:03", "After", -50, 900, 53.1, -1.2)).save(d, now=T0 + 1000)
            bt_log(("11:22:33:44:55:66", "Watch", -70, 12, 53.1, -1.2)).save(d, now=T0 + 600)
            with open(os.path.join(d, "wardrive-20990101-000000.wigle.csv"), "wb") as f:
                f.write(b"\xff\xfe not a log \x00\n")
            os.mkdir(os.path.join(d, "bluetooth-20990101-000000.wigle.csv"))       # cannot be read: skipped
            heard = wm.read_heard(T0, T0 + 600, d)
            self.assertEqual([(r["kind"], r["name"], r["rssi"], r["lat"]) for r in heard],
                             [("wifi", "Cafe", -55.0, 53.2), ("bt", "Watch", -70.0, 53.1)])
            self.assertEqual(wm.read_heard(T0, T0 + 600, os.path.join(d, "missing")), [])


class LayoutTests(unittest.TestCase):
    def test_north_is_up_east_is_right_and_everything_fits_in_the_box(self):
        track = wm.track_of([fix(0, 53.100, -1.200), fix(5, 53.101, -1.200), fix(10, 53.101, -1.198)])
        lay = wm.layout(track, [row(lat=53.1005, lon=-1.199)], 800, 400)
        (x0, y0), (x1, y1), (x2, y2) = lay["track"]
        self.assertAlmostEqual(x0, x1)
        self.assertLess(y1, y0)                                         # further north is higher up
        self.assertGreater(x2, x1)                                      # further east is further right
        for x, y in lay["track"] + lay["heard"]:
            self.assertTrue(0 <= x <= 800 and 0 <= y <= 400)
        self.assertEqual(len(lay["heard"]), 1)
        self.assertTrue(x0 < lay["heard"][0][0] < x2 and y1 < lay["heard"][0][1] < y0)

    def test_distances_on_the_screen_are_true_to_the_ground(self):
        track = wm.track_of([fix(0, 53.100, -1.200), fix(5, 53.101, -1.198)])
        lay = wm.layout(track, [], 800, 400)
        (x0, y0), (x1, y1) = lay["track"]
        on_screen = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 * lay["per_px"]
        self.assertAlmostEqual(on_screen, wm.gps_track.haversine_m(track[0], track[1]), delta=1.0)

    def test_a_mark_outside_the_track_still_fits(self):
        track = wm.track_of([fix(0, 53.100, -1.200), fix(5, 53.101, -1.200)])
        lay = wm.layout(track, [row(lat=53.2, lon=-1.0)], 600, 300)
        self.assertTrue(0 <= lay["heard"][0][0] <= 600 and 0 <= lay["heard"][0][1] <= 300)

    def test_standing_still_is_drawn_in_the_middle_and_gps_wander_is_not_blown_up(self):
        still = wm.layout(wm.track_of([fix(0, 53.1, -1.2), fix(5, 53.1, -1.2)]), [], 800, 400)
        self.assertEqual(still["track"], [(400.0, 200.0), (400.0, 200.0)])
        self.assertEqual(still["per_px"], wm.MIN_PER_PX)
        wander = wm.layout(wm.track_of([fix(0, 53.1, -1.2), fix(5, 53.100002, -1.2)]), [], 800, 400)
        (_x0, y0), (_x1, y1) = wander["track"]
        self.assertLess(abs(y1 - y0), 5)                                # a fifth of a metre is a few pixels, not the screen
        self.assertEqual(wander["per_px"], wm.MIN_PER_PX)

    def test_nothing_to_draw_gives_nothing(self):
        self.assertEqual(wm.layout([], [], 800, 400), {"track": [], "heard": [], "per_px": 0.0})

    def test_the_scale_bar_is_a_round_length_about_a_quarter_of_the_width(self):
        self.assertEqual(wm.scale_bar(1.0, 800), {"metres": 200, "px": 200.0, "text": "200 m"})
        self.assertEqual(wm.scale_bar(13.0, 800)["text"], "2.00 km")
        self.assertEqual(wm.scale_bar(wm.MIN_PER_PX, 800)["text"], "20 m")
        self.assertIsNone(wm.scale_bar(0.0, 800))
        self.assertIsNone(wm.scale_bar(1.0, 0))

    def test_a_tap_finds_the_nearest_mark_within_a_fingers_reach(self):
        places = [(100.0, 100.0), (110.0, 100.0), (300.0, 300.0)]
        self.assertEqual(wm.nearest(places, 108, 101, 16), 1)
        self.assertEqual(wm.nearest(places, 295, 290, 16), 2)
        self.assertIsNone(wm.nearest(places, 200, 200, 16))
        self.assertIsNone(wm.nearest([], 1, 1, 16))

    def test_marks_drawn_on_top_of_each_other_are_read_out_together(self):
        places = [(100.0, 100.0), (300.0, 300.0), (103.0, 101.0), (100.0, 100.0)]
        self.assertEqual(wm.together(places, 0), [0, 2, 3])
        self.assertEqual(wm.together(places, 1), [1])


class WordsTests(unittest.TestCase):
    """What the map says, and above all what it does not."""

    def test_a_mark_reads_as_name_address_signal_and_time(self):
        with mock.patch.object(wm.time, "localtime", time.gmtime):
            self.assertEqual(wm.mark_text(row(name="Cafe\n  Net")),
                             f"Wi-Fi  Cafe Net  aa:bb:cc:dd:ee:01  -60 dBm  first heard {time.strftime('%H:%M:%S', time.gmtime(T0 + 10))}")
        self.assertIn("Bluetooth  (no name)  11:22", wm.mark_text(row(kind="bt", addr="11:22:33:44:55:66", name="")))

    def test_a_tap_says_the_position_is_the_uconsoles_own(self):
        one = wm.picked_lines([row()])
        self.assertEqual(one[0], "The uConsole was here (53.10000, -1.20000) when it heard this strongest. "
                                 "That is not where it is.")
        self.assertEqual(len(one), 2)
        many = wm.picked_lines([row(addr=f"aa:bb:cc:dd:ee:{n:02d}") for n in range(8)])
        self.assertIn("when it heard these 8 strongest. That is not where they are.", many[0])
        self.assertEqual((len(many), many[-1]), (7, "and 3 more at the same spot"))
        self.assertEqual(wm.picked_lines([]), [])

    def test_the_legend_says_a_mark_is_where_the_uconsole_was_not_where_the_device_is(self):
        text = " ".join(wm.legend_lines())
        self.assertIn("where the uConsole was when it heard this strongest, not where the device is", text)
        self.assertIn("the uConsole's own track", text)
        self.assertIn("Square: a Wi-Fi network. Diamond: a Bluetooth address.", text)

    def test_nothing_anywhere_claims_to_know_where_a_device_is(self):
        said = " ".join(wm.legend_lines() + wm.picked_lines([row()]) + [wm.describe(row()), wm.status_line([1], [row()])]).lower()
        for claim in ("located", "location of", "position of the", "direction", "distance to", "is at", "person", "owner"):
            self.assertNotIn(claim, said)

    def test_no_fix_is_said_plainly(self):
        self.assertEqual(wm.status_line([], []), wm.NO_FIX)
        self.assertIn("No GPS fix during this session", wm.NO_FIX)

    def test_the_status_counts_what_is_on_the_map(self):
        track = wm.track_of([fix(0, 53.1, -1.2), fix(5, 53.1, -1.2)])
        self.assertIn("Reading the saved logs", wm.status_line(track, None))
        self.assertIn("No saved wardrive or Bluetooth log has anything first heard during this session", wm.status_line(track, []))
        self.assertEqual(wm.status_line(track, [row(), row(kind="bt"), row(kind="bt", addr="x")]),
                         "2 positions of the uConsole. 1 Wi-Fi network and 2 Bluetooth addresses from the saved logs, "
                         "first heard during this session.")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.track = wm.track_of([fix(0, 53.1, -1.2, 100.0), fix(5, 53.101, -1.201)])
        self.heard = [row(name="Tom & <Jerry>\x07"), row(kind="bt", addr="11:22:33:44:55:66", name="", at=12)]

    def test_the_gpx_holds_the_track_and_a_waypoint_for_each_thing_heard(self):
        root = ET.fromstring(wm.gpx_text(self.track, self.heard, "session-x"))
        points = root.findall(f"{GPX}trk/{GPX}trkseg/{GPX}trkpt")
        self.assertEqual([(p.get("lat"), p.get("lon")) for p in points], [("53.100000", "-1.200000"), ("53.101000", "-1.201000")])
        self.assertEqual(points[0].find(f"{GPX}ele").text, "100.0")
        self.assertEqual(root.find(f"{GPX}trk/{GPX}name").text, "session-x")
        wpts = root.findall(f"{GPX}wpt")
        self.assertEqual([w.find(f"{GPX}name").text for w in wpts], ["Tom & <Jerry>", "11:22:33:44:55:66"])
        self.assertEqual([w.find(f"{GPX}type").text for w in wpts], ["Wi-Fi", "Bluetooth"])
        self.assertEqual(wpts[0].find(f"{GPX}time").text, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(T0 + 10)))
        self.assertIn("where the uConsole was when it heard this strongest, not where the device is",
                      wpts[0].find(f"{GPX}desc").text)
        self.assertIn("-60 dBm", wpts[0].find(f"{GPX}desc").text)
        self.assertEqual([child.tag for child in root][-1], f"{GPX}trk")          # waypoints come before the track

    def test_a_gpx_with_nothing_heard_is_still_a_whole_file(self):
        root = ET.fromstring(wm.gpx_text(self.track, [], "x"))
        self.assertEqual(root.findall(f"{GPX}wpt"), [])
        self.assertEqual(len(root.findall(f"{GPX}trk/{GPX}trkseg/{GPX}trkpt")), 2)

    def test_the_geojson_is_a_line_for_the_track_and_a_point_with_properties_for_each_thing_heard(self):
        doc = json.loads(wm.geojson_text(self.track, self.heard, "session-x"))
        self.assertEqual(doc["type"], "FeatureCollection")
        line, wifi, bt = doc["features"]
        self.assertEqual(line["geometry"], {"type": "LineString", "coordinates": [[-1.2, 53.1], [-1.201, 53.101]]})
        self.assertEqual((line["properties"]["kind"], len(line["properties"]["times"])), ("track", 2))
        self.assertEqual(wifi["geometry"], {"type": "Point", "coordinates": [-1.2, 53.1]})
        self.assertEqual(wifi["properties"], {
            "kind": "heard", "type": "Wi-Fi", "name": "Tom & <Jerry>\x07", "address": "aa:bb:cc:dd:ee:01", "rssi_dbm": -60.0,
            "first_heard": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(T0 + 10)), "channel": "6",
            "security": "[WPA2-PSK-CCMP][ESS]",
            "position_is": "where the uConsole was when it heard this strongest, not where the device is"})
        self.assertEqual(bt["properties"]["type"], "Bluetooth")

    def test_one_fix_is_a_point_not_a_line_of_one(self):
        doc = json.loads(wm.geojson_text(self.track[:1], []))
        self.assertEqual(doc["features"][0]["geometry"], {"type": "Point", "coordinates": [-1.2, 53.1]})
        self.assertEqual(json.loads(wm.geojson_text([], []))["features"], [])


class FileTests(unittest.TestCase):
    """The exports are private, sit beside the session, and go when it goes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        w = ev.Writer(T0)
        for rec in (fix(0, 53.1, -1.2), fix(5, 53.101, -1.201)):
            w.write(rec)
        w.close()
        self.path = w.path
        self.track = wm.track_of(ev.load(self.path).samples)

    def test_exports_are_private_files_beside_the_session(self):
        for kind in ("gpx", "geojson"):
            path = wm.export(kind, self.path, self.track, [row()])
            self.assertEqual(path, self.path[:-len("jsonl")] + kind)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertIn("aa:bb:cc:dd:ee:01", open(path).read())
        self.assertEqual(len(ev.all_files()), 3)
        self.assertEqual(ev.files(), [self.path])                       # an export is not a session in the picker
        with self.assertRaises(ValueError):
            wm.export("kml", self.path, self.track, [])

    def test_exporting_again_replaces_the_file_and_keeps_it_private(self):
        path = wm.export("gpx", self.path, self.track, [])
        os.chmod(path, 0o644)
        self.assertEqual(wm.export("gpx", self.path, self.track, [row()]), path)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(len(ET.fromstring(open(path).read()).findall(f"{GPX}wpt")), 1)

    def test_an_export_is_never_written_through_a_symlink(self):
        target = os.path.join(self.tmp.name, "elsewhere.txt")
        open(target, "w").close()
        os.symlink(target, wm.export_path(self.path, "gpx"))
        with self.assertRaises(OSError):
            wm.export("gpx", self.path, self.track, [])
        self.assertEqual(open(target).read(), "")

    def test_delete_removes_the_session_with_every_file_exported_from_it(self):
        ev.export_csv(ev.load(self.path))
        gpx = wm.export("gpx", self.path, self.track, [])
        wm.export("geojson", self.path, self.track, [])
        self.assertEqual(ev.delete(gpx), 0)                             # only ever by the session itself
        self.assertEqual(ev.delete(self.path), 4)
        self.assertEqual(ev.all_files(), [])

    def test_clear_removes_the_exports_with_the_other_logs(self):
        wm.export("gpx", self.path, self.track, [])
        wm.export("geojson", self.path, self.track, [])
        self.assertEqual(reports.clear_all(), 3)
        self.assertEqual(os.listdir(reports.state_dir()), [])

    def test_an_export_never_goes_into_a_problem_report(self):
        wm.export("geojson", self.path, self.track, [row()])
        with mock.patch.object(reports, "_run", lambda *_a, **_k: "ok"):
            text = reports.build_report(["Everything"])
        for private in ("53.1", "aa:bb:cc", "geojson", "session-"):
            self.assertNotIn(private, text)


class PassiveTests(unittest.TestCase):
    def setUp(self):
        with open(wm.__file__) as f:
            self.source = f.read()
        self.tree = ast.parse(self.source)

    def test_it_reads_files_and_draws_and_does_nothing_else(self):
        imported, names = set(), set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        self.assertEqual(imported & {"subprocess", "socket", "urllib", "http", "requests", "shlex"}, set())
        for forbidden in ("system", "set_rail", "run_privileged", "systemctl", "send", "sendto", "urlopen", "connect_engine"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_qt_is_only_imported_inside_build_view(self):
        for node in self.tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.assertNotIn("PyQt6", ast.dump(node))
        self.assertIn("def build_view(win):\n    from PyQt6", self.source)


if __name__ == "__main__":
    unittest.main()
