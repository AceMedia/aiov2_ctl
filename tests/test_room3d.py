import math
import unittest

from ops_gui import room3d as r, sense, sense_dsp as d


class GeometryTests(unittest.TestCase):
    def test_strong_signals_are_nearer(self):
        self.assertLess(r.radius_of(-40), r.radius_of(-70))
        self.assertLessEqual(r.radius_of(-100), r.FLOOR)
        self.assertGreaterEqual(r.radius_of(-10), 0.3 * r.FLOOR)
        self.assertEqual(r.radius_of(None), 0.65 * r.FLOOR)

    def test_a_post_keeps_its_place_however_loud_it_is(self):
        quiet = [{"name": n, "db": 0.0, "dbm": -60.0} for n in ("b", "a", "c")]
        loud = [{"name": n, "db": 15.0, "dbm": -60.0} for n in ("c", "b", "a")]
        pq, pl = r.layout(quiet), r.layout(loud)
        self.assertEqual([(p["name"], round(p["x"], 6), round(p["z"], 6)) for p in pq],
                         [(p["name"], round(p["x"], 6), round(p["z"], 6)) for p in pl])
        self.assertEqual([p["name"] for p in pq], ["a", "b", "c"])

    def test_posts_are_spread_apart_and_rise_with_disturbance(self):
        posts = r.layout([{"name": f"n{i}", "db": i * 4.0, "dbm": -60.0} for i in range(6)])
        for i, a in enumerate(posts):
            for b in posts[i + 1:]:
                self.assertGreater(math.hypot(a["x"] - b["x"], a["z"] - b["z"]), 0.5)
        self.assertEqual([p["height"] for p in posts], sorted(p["height"] for p in posts))
        self.assertLessEqual(max(p["height"] for p in posts), r.MAX_HEIGHT + 0.07)

    def test_empty_and_bad_values_are_safe(self):
        self.assertEqual(r.layout([]), [])
        p = r.layout([{"name": "a", "db": None, "dbm": None}, {"name": "b", "db": -5.0}])
        self.assertTrue(all(x["height"] >= 0.06 for x in p))
        self.assertEqual(r.layout([{"name": "a", "db": 1.0, "label": "Home"}])[0]["label"], "Home")

    def test_projection_is_dimetric_and_depth_grows_towards_the_viewer(self):
        x0, y0, _ = r.project(0, 0, 0, scale=100, cx=200, cy=150)
        self.assertEqual((x0, y0), (200, 150))
        _, y_up, _ = r.project(0, 1, 0, scale=100, cx=200, cy=150)
        self.assertLess(y_up, y0)
        yaw = r.YAWS[0]
        _, y_near, d_near = r.project(0.5, 0, 0.5, yaw, 100, 0, 0)
        _, y_far, d_far = r.project(-0.5, 0, -0.5, yaw, 100, 0, 0)
        self.assertGreater(d_near, d_far)
        self.assertGreater(y_near, y_far)                                  # nearer is lower on the screen
        xa, ya, _ = r.project(1, 0, 0, yaw, 100)
        self.assertAlmostEqual(abs(ya) * 2, abs(xa), places=6)             # 2:1 steps

    def test_views_snap_to_four_tidy_angles(self):
        self.assertEqual(r.snap_yaw(0.0), r.YAWS[0])
        self.assertEqual(r.snap_yaw(math.radians(100)), r.YAWS[1])
        self.assertEqual(r.snap_yaw(math.radians(-80)), r.YAWS[3])
        self.assertEqual(len(set(round(y, 6) for y in r.YAWS)), 4)

    def test_the_scene_fits_the_widget(self):
        for w, h in ((300, 260), (500, 380), (240, 230)):
            for yaw in r.YAWS:
                scale, cx, cy = r.fit(w, h, yaw)
                for x in (-r.FLOOR, r.FLOOR):
                    for z in (-r.FLOOR, r.FLOOR):
                        sx, sy, _ = r.project(x, 0, z, yaw, scale, cx, cy)
                        self.assertTrue(0 <= sx <= w and 0 <= sy <= h, (w, h, sx, sy))
                _, top, _ = r.project(0, r.MAX_HEIGHT, 0, yaw, scale, cx, cy)
                self.assertGreaterEqual(top, 0)

    def test_levels_and_colours(self):
        self.assertEqual([r.level(v) for v in (0, 2.9, 3, 6, 10, 16, 40, None)], [0, 0, 1, 2, 3, 4, 4, 0])
        self.assertEqual(r.heat_colour(0), r.HEAT[0][1])
        self.assertEqual(r.heat_colour(99), r.HEAT[-1][1])
        for db in (0, 2, 5, 8, 12, 17, 30):
            self.assertRegex(r.heat_colour(db), r"^#[0-9a-fA-F]{6}$")
        for _v, colour in r.HEAT:                                          # no blues: the palette is Debian red and orange
            self.assertGreaterEqual(int(colour[1:3], 16), int(colour[5:7], 16))

    def test_ghost_caps_skip_the_newest_height(self):
        self.assertEqual(sense.q_trail([]), [])
        self.assertEqual(sense.q_trail([1.0]), [])
        trail = list(range(13))
        caps = sense.q_trail(trail)
        self.assertLessEqual(len(caps), 3)
        self.assertNotIn(12, caps)

    def test_it_only_shows_disturbance_not_people(self):
        import inspect
        src = inspect.getsource(r)
        self.assertIn("not where a person", src)
        self.assertNotIn("crc32", src)                                     # no invented bearings


class SharpChangeTests(unittest.TestCase):
    def run_series(self, series, **kw):
        w, out = d.SharpChange(**kw), []
        for i, db in enumerate(series):
            if w.update(db, float(i)):
                out.append(i)
        return out

    def test_a_burst_then_stillness_is_noted_once(self):
        self.assertEqual(len(self.run_series([1] * 10 + [22, 24, 18] + [1] * 40)), 1)

    def test_steady_movement_is_not(self):
        self.assertEqual(self.run_series([1] * 10 + [8, 12, 15, 10, 9, 12, 14, 9] * 6 + [8] * 10), [])

    def test_a_burst_followed_by_more_movement_is_not(self):
        self.assertEqual(self.run_series([1] * 10 + [22, 24] + [9] * 30 + [1] * 10), [])

    def test_stillness_alone_is_not(self):
        self.assertEqual(self.run_series([0.5] * 200), [])

    def test_only_once_in_the_cooldown(self):
        series = ([1] * 5 + [22, 24] + [1] * 40) * 2
        self.assertEqual(len(self.run_series(series)), 1)
        self.assertEqual(len(self.run_series(series, cooldown_s=10.0)), 2)

    def test_it_is_logged_in_plain_signal_words_with_no_guess_at_a_cause(self):
        self.assertTrue(sense.event_text("sharp_then_still").endswith("  a sharp change, then still"))


if __name__ == "__main__":
    unittest.main()
