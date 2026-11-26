import os
import stat
import tempfile
import unittest

from ops_gui import gps_track as g


def fix(lat, lon, t=0, mode=3, alt=10.0, speed=1.0):
    return {"lat": lat, "lon": lon, "mode": mode, "alt": alt, "speed": speed}


class FormatTests(unittest.TestCase):
    def test_decimal(self):
        self.assertEqual(g.format_position(51.5074, -0.1278), "51.50740 N, 0.12780 W")
        self.assertEqual(g.format_position(None, 1), "")

    def test_degrees_minutes(self):
        self.assertEqual(g.format_position(51.5, -0.25, "dm"), "51 30.000' N, 0 15.000' W")

    def test_dms(self):
        self.assertEqual(g.format_position(51.5, -0.25, "dms"), "51 30' 00.0\" N, 0 15' 00.0\" W")

    def test_maidenhead(self):
        self.assertEqual(g.maidenhead(51.5074, -0.1278), "IO91wm")
        self.assertEqual(g.maidenhead(-34.9, 138.6, 2), "PF95")
        self.assertEqual(g.maidenhead(0, 0, 1), "JJ")
        self.assertEqual(g.maidenhead(91, 0), "")
        self.assertEqual(g.format_position(51.5074, -0.1278, "grid"), "IO91wm")


class TrackTests(unittest.TestCase):
    def test_haversine(self):
        d = g.haversine_m({"lat": 0, "lon": 0}, {"lat": 0, "lon": 1})
        self.assertAlmostEqual(d, 111195, delta=200)

    def test_only_real_moving_fixes_are_added(self):
        t = []
        self.assertFalse(g.add_point(t, fix(1, 1, mode=1)))
        self.assertFalse(g.add_point(t, {"mode": 3}))
        self.assertFalse(g.add_point(t, None))
        self.assertTrue(g.add_point(t, fix(51.0, 0.0), now=1))
        self.assertFalse(g.add_point(t, fix(51.0, 0.000001), now=2))     # under 3 m
        self.assertTrue(g.add_point(t, fix(51.001, 0.0), now=3))
        self.assertEqual(len(t), 2)

    def test_cap(self):
        t = [{"lat": 0, "lon": i * 0.001, "t": i} for i in range(g.MAX_POINTS)]
        self.assertFalse(g.add_point(t, fix(10, 10)))

    def test_stats(self):
        t = []
        g.add_point(t, fix(51.0, 0.0, speed=2.0, alt=5), now=0)
        g.add_point(t, fix(51.001, 0.0, speed=4.0, alt=9), now=100)
        s = g.track_stats(t)
        self.assertEqual(s["points"], 2)
        self.assertAlmostEqual(s["distance"], 111, delta=2)
        self.assertEqual(s["duration"], 100)
        self.assertEqual((s["max"], s["alt_min"], s["alt_max"]), (4.0, 5, 9))
        self.assertEqual(g.track_stats([])["points"], 0)

    def test_text(self):
        self.assertEqual(g.distance_text(850), "850 m")
        self.assertEqual(g.distance_text(1500), "1.50 km")
        self.assertEqual(g.duration_text(75), "1m 15s")
        self.assertEqual(g.duration_text(3725), "1h 02m")
        self.assertEqual(g.speed_kmh(None), "-")
        self.assertEqual(dict(g.stats_lines(g.track_stats([])))["Altitude"], "-")


class DrawTests(unittest.TestCase):
    def test_projection_fits_and_is_north_up(self):
        t = [{"lat": 51.0, "lon": 0.0, "t": 0}, {"lat": 51.01, "lon": 0.0, "t": 1}]
        pts, per_px = g.project(t, 200, 100)
        self.assertGreater(per_px, 0)
        self.assertLess(pts[1][1], pts[0][1])             # north is up
        for x, y in pts:
            self.assertTrue(0 <= x <= 200 and 0 <= y <= 100)

    def test_single_point_and_empty(self):
        self.assertEqual(g.project([], 100, 100), ([], 0.0))
        pts, per_px = g.project([{"lat": 1, "lon": 1, "t": 0}], 100, 80)
        self.assertEqual((pts, per_px), ([(50.0, 40.0)], 0.0))

    def test_nice_length(self):
        self.assertEqual(g.nice_length(730), 500)
        self.assertEqual(g.nice_length(1900), 1000)
        self.assertEqual(g.nice_length(2100), 2000)
        self.assertEqual(g.nice_length(0), 0)


class FileTests(unittest.TestCase):
    def test_gpx_and_private_file(self):
        t = [{"lat": 51.0, "lon": 0.0, "alt": 5.0, "t": 0}, {"lat": 51.001, "lon": 0.0, "alt": None, "t": 60}]
        text = g.gpx_text(t, "a<b>")
        self.assertIn('<trkpt lat="51.000000" lon="0.000000"><ele>5.0</ele><time>1970-01-01T00:00:00Z', text)
        self.assertIn("a&lt;b&gt;", text)
        with tempfile.TemporaryDirectory() as d:
            path = g.new_track_path(os.path.join(d, "gps"), now=0)
            g.write_track(t, path)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
            g.write_track(t[:1], path)                      # rewriting is fine
            self.assertEqual(open(path).read().count("<trkpt"), 1)

    def test_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "real")
            open(target, "w").close()
            link = os.path.join(d, "link.gpx")
            os.symlink(target, link)
            with self.assertRaises(OSError):
                g.write_track([{"lat": 1, "lon": 1, "alt": None, "t": 0}], link)

    def test_read_waypoints_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "w.csv")
            with open(path, "w") as f:
                f.write("time,lat,lon,alt_m\n2026-01-01T10:00:00+0000,51.1,0.1,5.0\nbad\n"
                        "2026-01-02T10:00:00+0000,52.2,0.2,\n")
            rows = g.read_waypoints(path)
            self.assertEqual([r["lat"] for r in rows], [52.2, 51.1])
            self.assertEqual(g.read_waypoints(os.path.join(d, "none")), [])

    def test_it_has_no_network_code(self):
        import inspect
        src = inspect.getsource(g)
        for word in ("socket", "urllib", "requests", "subprocess"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
