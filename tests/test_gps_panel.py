"""Pure helpers behind the GPS tab."""
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from ops_gui import gps_panel as gp  # noqa: E402


class GpsPanelTests(unittest.TestCase):
    def test_fix_labels(self):
        self.assertEqual(gp.fix_label(3), "3D fix")
        self.assertEqual(gp.fix_label(None), "No fix")

    def test_coordinates(self):
        self.assertEqual(gp.format_coords(51.5074, -0.1278), "51.50740 N, 0.12780 W")
        self.assertEqual(gp.format_coords(-33.9, 151.2, 2), "33.90 S, 151.20 E")
        self.assertEqual(gp.format_coords(None, 1), "")

    def test_compass(self):
        self.assertEqual(gp.compass_point(0), "N")
        self.assertEqual(gp.compass_point(90), "E")
        self.assertEqual(gp.compass_point(359), "N")
        self.assertEqual(gp.compass_point(225), "SW")
        self.assertEqual(gp.compass_point(None), "-")

    def test_speed(self):
        self.assertEqual(gp.speed_text(10), "36.0 km/h  (22.4 mph)")
        self.assertEqual(gp.speed_text(None), "-")

    def test_sky_positions(self):
        x, y = gp.sky_xy(0, 0, 100)          # due north on the horizon
        self.assertAlmostEqual(x, 0, places=6)
        self.assertAlmostEqual(y, -100, places=6)
        x, y = gp.sky_xy(90, 0, 100)         # due east on the horizon
        self.assertAlmostEqual(x, 100, places=6)
        self.assertAlmostEqual(gp.sky_xy(123, 90, 100)[0], 0, places=6)   # zenith is the centre
        self.assertIsNone(gp.sky_xy(None, 10, 100))

    def test_sat_rows_sorted_and_filtered(self):
        rows = gp.sat_rows([{"prn": 1, "ss": 20, "used": False}, {"prn": 2, "ss": 40, "used": True},
                            {"prn": 3, "ss": 0}, {"prn": 4, "ss": 20, "used": True}])
        self.assertEqual([r["prn"] for r in rows], [2, 4, 1])

    def test_waypoints_are_private_and_need_a_position(self):
        self.assertIsNone(gp.waypoint_line({"lat": None, "lon": 1}))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x", "waypoints.csv")
            self.assertEqual(gp.save_waypoint({"lat": 51.5, "lon": -0.1, "alt": 12.34}, path), path)
            gp.save_waypoint({"lat": 51.6, "lon": -0.2}, path)
            lines = open(path).read().splitlines()
            self.assertEqual(lines[0], "time,lat,lon,alt_m")
            self.assertEqual(len(lines), 3)
            self.assertIn("51.500000,-0.100000,12.3", lines[1])
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertIsNone(gp.save_waypoint(None, path))


if __name__ == "__main__":
    unittest.main()
