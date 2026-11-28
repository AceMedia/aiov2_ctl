"""The screensavers' plain parts (ops_gui/screensaver.py). No screen is needed."""
import random
import unittest

from ops_gui import screensaver as ss


class WhenTests(unittest.TestCase):
    def test_it_is_off_unless_switched_on(self):
        self.assertFalse(ss.due(10_000, False, 1))
        self.assertFalse(ss.due(10_000, None, 1))

    def test_it_comes_on_after_the_chosen_wait_and_not_before(self):
        self.assertFalse(ss.due(299, True, 5))
        self.assertTrue(ss.due(300, True, 5))
        self.assertTrue(ss.due(60, True, 1))

    def test_a_wait_that_is_not_one_of_the_choices_falls_back_to_the_usual(self):
        for odd in (None, "soon", 0, 7, -1):
            self.assertEqual(ss.minutes_of(odd), ss.DEFAULT_MINUTES, odd)
        self.assertEqual(ss.minutes_of("10"), 10)
        self.assertFalse(ss.due(60, True, 0), "a wait of nothing would cover the app for ever")

    def test_an_unknown_screensaver_falls_back_to_the_usual(self):
        self.assertEqual(ss.kind_of("fireworks"), ss.DEFAULT_KIND)
        self.assertEqual(ss.kind_of(None), ss.DEFAULT_KIND)
        for key, _name in ss.KINDS:
            self.assertEqual(ss.kind_of(key), key)

    def test_there_is_a_mandelbrot_and_a_system_one(self):
        keys = [k for k, _ in ss.KINDS]
        self.assertIn("mandelbrot", keys)
        self.assertIn("system", keys)
        self.assertEqual(len(keys), len(set(keys)))

    def test_pointer_movement_counts_only_after_a_moment_and_only_if_it_is_real(self):
        self.assertFalse(ss.moved_enough((100, 100), (300, 300), 0.2), "showing it can itself move the pointer")
        self.assertFalse(ss.moved_enough((100, 100), (102, 101), 5.0), "a twitch")
        self.assertTrue(ss.moved_enough((100, 100), (120, 100), 5.0))
        self.assertFalse(ss.moved_enough(None, (120, 100), 5.0))


class MandelbrotTests(unittest.TestCase):
    def test_points_in_the_set_never_leave_and_points_far_outside_leave_at_once(self):
        self.assertEqual(ss.escape(0.0, 0.0, 50), 50)
        self.assertEqual(ss.escape(-1.0, 0.0, 50), 50)
        self.assertEqual(ss.escape(2.5, 2.5, 50), 1)
        self.assertLess(ss.escape(0.5, 0.5, 50), 10)

    def test_the_dive_starts_from_the_whole_set_and_ends_on_its_target(self):
        target = ss.TARGETS[0]
        cx, cy, span = ss.view(0, target)
        self.assertEqual((cx, cy), (-0.6, 0.0))
        self.assertAlmostEqual(span, ss.START_SPAN)
        cx, cy, span = ss.view(10, target)
        self.assertAlmostEqual(cx, target[0])
        self.assertAlmostEqual(cy, target[1])
        self.assertLess(span, ss.START_SPAN / 50)

    def test_each_level_is_closer_and_looks_harder(self):
        spans = [ss.view(n, ss.TARGETS[0])[2] for n in range(ss.LEVELS)]
        self.assertEqual(spans, sorted(spans, reverse=True))
        self.assertGreater(ss.limit_for(20), ss.limit_for(0))
        self.assertGreater(spans[-1], 1e-9, "still far wider than a float can no longer tell apart")

    def test_every_target_is_on_the_edge_where_there_is_something_to_see(self):
        for target in ss.TARGETS:
            cx, cy, span = ss.view(12, target)
            counts = {n for y in range(0, 24, 3) for n in ss.row(y, 32, 24, cx, cy, span, ss.limit_for(12))}
            self.assertGreater(len(counts), 6, target)

    def test_a_row_is_one_count_per_block_and_the_picture_is_symmetrical_on_the_real_axis(self):
        top = ss.row(2, 40, 30, -0.6, 0.0, 3.4, 60)
        bottom = ss.row(27, 40, 30, -0.6, 0.0, 3.4, 60)
        self.assertEqual(len(top), 40)
        self.assertEqual(top, bottom)

    def test_the_set_is_the_background_colour_and_the_rest_cycles_through_the_ramp(self):
        colours = ss.ramp("#000000", "#4c8dff", "#ff8a00")
        self.assertEqual(colours[0], "#000000")
        self.assertIn("#4c8dff", colours)
        self.assertIn("#ff8a00", colours)
        self.assertEqual(ss.shade(60, 60, colours), colours[0])
        self.assertEqual(ss.shade(0, 60, colours), colours[1])
        self.assertEqual(ss.shade(len(colours) - 1, 600, colours), colours[1], "it wraps round")
        for colour in colours:
            self.assertRegex(colour, r"^#[0-9a-f]{6}$")

    def test_after_the_deepest_picture_it_starts_again_somewhere_else(self):
        self.assertEqual(ss.next_dive(3, 1), (4, 1))
        self.assertEqual(ss.next_dive(ss.LEVELS - 1, 1), (0, 2))
        self.assertEqual(ss.next_dive(ss.LEVELS - 1, len(ss.TARGETS) - 1), (0, 0))


class StarTests(unittest.TestCase):
    def test_a_star_comes_closer_and_one_that_passes_starts_again_far_away(self):
        rng = random.Random(1)
        star = [0.1, 0.1, 0.5]
        ss.star_step(star, 1.0, rng)
        self.assertLess(star[2], 0.5)
        star = [0.1, 0.1, 0.03]
        ss.star_step(star, 1.0, rng)
        self.assertGreaterEqual(star[2], 0.6)

    def test_a_near_star_is_bigger_and_brighter_and_one_off_the_screen_is_not_drawn(self):
        far = ss.star_at([0.1, 0.1, 0.95], 1280, 696)
        near = ss.star_at([0.02, 0.02, 0.1], 1280, 696)
        self.assertGreater(near[2], far[2])
        self.assertGreater(near[3], far[3])
        self.assertIsNone(ss.star_at([0.9, 0.9, 0.1], 1280, 696))


class RainTests(unittest.TestCase):
    def test_drops_fall_and_start_again_above_the_top(self):
        rng = random.Random(2)
        drops = [{"col": 3, "y": 10.0, "speed": 10.0, "length": 5}, {"col": 5, "y": 60.0, "speed": 10.0, "length": 5}]
        ss.rain_step(drops, 44, 1.0, rng)
        self.assertEqual(drops[0]["y"], 20.0)
        self.assertEqual(drops[1]["col"], 5)
        self.assertLessEqual(drops[1]["y"], 0)

    def test_a_drop_is_brightest_at_its_head_and_only_its_cells_on_the_screen_are_drawn(self):
        cells = ss.drop_cells({"col": 0, "y": 2.4, "speed": 1, "length": 5}, 44)
        self.assertEqual([r for r, _ in cells], [2, 1, 0])
        self.assertEqual(cells[0][1], 1.0)
        self.assertGreater(cells[0][1], cells[-1][1])
        self.assertEqual(ss.drop_cells({"col": 0, "y": -3.0, "speed": 1, "length": 5}, 44), [])


class ClockTests(unittest.TestCase):
    def test_the_clock_stays_on_the_screen_and_never_stays_put(self):
        seen = set()
        for t in range(0, 4000, 7):
            x, y = ss.drift(t, 1280, 696, 520, 190)
            self.assertTrue(0 <= x <= 1280 - 520 and 0 <= y <= 696 - 190, (t, x, y))
            seen.add((x, y))
        self.assertGreater(len(seen), 300)

    def test_a_window_smaller_than_the_clock_does_not_send_it_off_the_edge(self):
        self.assertEqual(ss.drift(123, 400, 100, 520, 190), (0, 0))


class SystemTests(unittest.TestCase):
    def test_the_history_keeps_only_the_last_readings_and_fills_a_gap_with_the_last_one(self):
        h = []
        for v in range(200):
            ss.push(h, v, keep=50)
        self.assertEqual(len(h), 50)
        self.assertEqual(h[-1], 199.0)
        ss.push(h, None, keep=50)
        self.assertEqual(h[-1], 199.0)
        self.assertEqual(ss.push([], None), [0.0])

    def test_the_graph_puts_the_newest_reading_at_the_right_and_keeps_inside_its_box(self):
        pts = ss.graph_points([0, 50, 100, 250], 0, 100, 400, 100)
        self.assertEqual(pts[-1], (400, 0))                    # over the top is held at the top
        self.assertEqual([y for _, y in pts[:3]], [100, 50, 0])
        self.assertEqual([x for x, _ in pts], sorted(x for x, _ in pts))
        self.assertEqual(ss.graph_points([], 0, 100, 400, 100), [])
        self.assertEqual(ss.graph_points([1, 2], 5, 5, 400, 100), [])

    def test_a_bar_lights_in_proportion_and_a_missing_reading_lights_nothing(self):
        self.assertEqual(ss.lit(50, 100, 40), 20)
        self.assertEqual(ss.lit(100, 100, 40), 40)
        self.assertEqual(ss.lit(130, 100, 40), 40)
        self.assertEqual(ss.lit(None, 100, 40), 0)
        self.assertEqual(ss.lit(-5, 100, 40), 0)


class WakingTests(unittest.TestCase):
    """What the window does with it is in app.py; these hold the lines that matter."""

    def test_the_key_or_button_that_wakes_the_app_does_nothing_else(self):
        import inspect
        from ops_gui import app
        source = inspect.getsource(app)
        self.assertIn("return lambda *a: None if self.typing() or self.saver_blocks() else fn()", source)
        self.assertIn("if self.saver_wake():", source[source.index("def act(self, action)"):][:300])
        self.assertIn("QEvent.Type.ShortcutOverride", source)

    def test_it_never_switches_a_rail_or_saves_anything_itself(self):
        import inspect
        text = inspect.getsource(ss)
        for word in ("toggle_rail", "subprocess", "save_settings", "aiov2_ctl"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
