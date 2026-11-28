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


class SignalsTests(unittest.TestCase):
    def test_it_is_one_of_the_kinds(self):
        self.assertEqual(dict(ss.KINDS)["signals"], "Signals")
        self.assertEqual(ss.kind_of("signals"), "signals")

    def test_nothing_heard_gives_no_bands_and_no_layout(self):
        for empty in (None, {}, {"wifi": [], "bt": None}, {"wifi": [{"rssi": None}, {"rssi": "strong"}, "junk"]}):
            self.assertEqual(ss.signal_bands(empty), [], empty)
        self.assertEqual(ss.signal_layout([], 1280, 720, 0.0), [])

    def test_missing_sources_are_left_out_and_the_order_is_fixed(self):
        bands = ss.signal_bands({"gps": [{"ss": 30}], "wifi": [{"rssi": -60}]})
        self.assertEqual([b["key"] for b in bands], ["wifi", "gps"])
        bands = ss.signal_bands({"gps": [{"ss": 30}], "mesh": [{"snr": 1}], "bt": [{"rssi": -70}], "wifi": [{"rssi": -60}]})
        self.assertEqual([b["key"] for b in bands], ["wifi", "bt", "mesh", "gps"])

    def test_the_sdr_is_never_drawn(self):
        self.assertEqual(ss.signal_bands({"sdr": [{"rssi": -40, "ss": 40, "snr": 5}]}), [])

    def test_strengths_are_clamped_to_nought_and_one(self):
        bars = ss.signal_bands({"wifi": [{"rssi": -120}, {"rssi": -100}, {"rssi": -65}, {"rssi": -30}, {"rssi": -10}]})[0]["bars"]
        self.assertEqual(bars, [1.0, 1.0, 0.5, 0.0, 0.0])
        self.assertEqual(ss.strength(25, 0, 50), 0.5)
        self.assertIsNone(ss.strength(True, 0, 50))
        self.assertIsNone(ss.strength(float("nan"), 0, 50))
        self.assertEqual(ss.signal_bands({"mesh": [{"snr": 99}]})[0]["bars"], [1.0])

    def test_captions_give_an_honest_count(self):
        bands = ss.signal_bands({"wifi": [{"rssi": -50}] * 12, "bt": [{"rssi": -50}], "gps": [{"ss": 20}, {"ss": None}]})
        self.assertEqual([b["caption"] for b in bands], ["WI-FI  12 NETWORKS", "BLUETOOTH  1 DEVICE", "GPS  1 SATELLITE"])
        for b in bands:
            for word in ("METRE", "AWAY", "PERSON", "NEAR"):
                self.assertNotIn(word, b["caption"])

    def test_the_bars_are_strongest_first_and_the_order_is_stable(self):
        items = [{"id": "b", "rssi": -60}, {"id": "a", "rssi": -60}, {"id": "c", "rssi": -40}]
        one = ss.signal_bands({"bt": items})
        two = ss.signal_bands({"bt": list(reversed(items))})
        self.assertEqual(one, two)
        self.assertEqual(one[0]["bars"][0], ss.strength(-40, -100, -30))

    def test_the_table_text_gives_its_number(self):
        self.assertEqual(ss.dbm_of("-50 dBm"), -50.0)
        self.assertIsNone(ss.dbm_of("-"))
        self.assertIsNone(ss.dbm_of(None))

    def test_many_more_than_fit_are_cut_to_what_fits_and_stay_on_the_screen(self):
        bands = ss.signal_bands({"wifi": [{"id": i, "rssi": -100 + i % 70} for i in range(2000)], "gps": [{"ss": 40}]})
        self.assertEqual(bands[0]["caption"], "WI-FI  2000 NETWORKS")
        for t in (0.0, 13.7, 1e6):
            layout = ss.signal_layout(bands, 1280, 720, t)
            self.assertEqual(len(layout), 2)
            xs = [x for x, *_ in layout[0]["bars"]]
            self.assertLess(len(xs), 2000)
            self.assertEqual(len(xs), len(set(xs)), "no two bars in one place")
            for band in layout:
                for x, y, w, h, k in band["bars"]:
                    self.assertTrue(0 <= x and x + w <= 1280 and 0 <= y and y + h <= 720)
                    self.assertGreaterEqual(h, 2)

    def test_the_bands_stack_without_overlapping_and_taller_bars_are_stronger(self):
        bands = ss.signal_bands({"wifi": [{"rssi": -30}, {"rssi": -90}], "bt": [{"rssi": -50}]})
        layout = ss.signal_layout(bands, 1280, 720, 0.0)
        wifi_bottom = max(y + h for _, y, _, h, _ in layout[0]["bars"])
        self.assertLess(wifi_bottom, layout[1]["caption"][1])
        heights = [h for _, _, _, h, _ in layout[0]["bars"]]
        self.assertGreater(heights[0], heights[1])

    def test_the_bars_drift(self):
        bands = ss.signal_bands({"wifi": [{"rssi": -50}]})
        xs = {ss.signal_layout(bands, 1280, 720, t)[0]["bars"][0][0] for t in range(0, 60, 5)}
        self.assertGreater(len(xs), 3)

    def test_a_tiny_screen_gets_nothing_rather_than_an_error(self):
        self.assertEqual(ss.signal_layout(ss.signal_bands({"wifi": [{"rssi": -50}]}), 40, 40, 0.0), [])


class StarInkTests(unittest.TestCase):
    def test_most_stars_are_plain_and_some_take_the_feature_colour_and_its_partner(self):
        inks = [ss.star_ink(i, "#ffffff", "#4c8dff", "#ff8a00") for i in range(240)]
        self.assertEqual(inks.count("#4c8dff"), 60, "one in four")
        self.assertEqual(inks.count("#ff8a00"), 20, "one in twelve")
        self.assertEqual(inks.count("#ffffff"), 160)
        self.assertEqual(ss.star_ink(1, "t", "a", "b"), "t")


class MandelbrotTests(unittest.TestCase):
    def test_points_in_the_set_never_leave_and_points_far_outside_leave_at_once(self):
        self.assertEqual(ss.smooth(0.0, 0.0, 50), -1.0)
        self.assertEqual(ss.smooth(-1.0, 0.0, 50), -1.0)
        self.assertLess(ss.smooth(2.5, 2.5, 50), 4)
        self.assertGreaterEqual(ss.smooth(2.5, 2.5, 50), 0.0)
        self.assertLess(ss.smooth(0.5, 0.5, 50), 10)

    def test_the_count_changes_evenly_so_there_are_no_bands(self):
        # along a line running away from the set the whole-number count falls in steps; this one falls evenly
        values = [ss.smooth(0.30 + i * 0.002, 0.0, 400) for i in range(200)]
        self.assertTrue(all(v >= 0 for v in values))
        self.assertTrue(any(v != int(v) for v in values), "it has a fraction")
        steps = [a - b for a, b in zip(values, values[1:])]
        self.assertTrue(all(step > 0 for step in steps), "always falling, never flat then a jump")
        self.assertLess(max(steps[100:]), 0.5)

    def test_the_dive_starts_from_the_whole_set_and_ends_on_its_target(self):
        for target in ss.TARGETS:
            cx, cy, span = ss.view(0, target)
            self.assertAlmostEqual(span, ss.START_SPAN)
            self.assertLess(cx - span / 2, -1.9, "all but the very tip of the set is in the first picture")
            self.assertGreater(cx + span / 2, 0.47, "and so is its right edge")
            cx, cy, span = ss.view(ss.LEVELS - 1, target)
            self.assertLess(abs(cx - target[0]), span / 2, "the target is still in the last picture")
            self.assertLess(abs(cy - target[1]), span * 9 / 16 / 2)

    def test_each_level_is_closer_and_looks_harder(self):
        spans = [ss.view(n, ss.TARGETS[0])[2] for n in range(ss.LEVELS)]
        self.assertEqual(spans, sorted(spans, reverse=True))
        self.assertGreater(ss.limit_for(20), ss.limit_for(0))
        self.assertGreater(spans[-1], 1e-9, "still far wider than a float can no longer tell apart")

    def test_every_picture_lies_inside_the_one_before_so_magnifying_never_shows_an_edge(self):
        cols, rows = 640, 360
        for target in ss.TARGETS:
            for level in range(ss.LEVELS - 1):
                for part in (0.0, 0.25, 0.5, 0.75, 1.0):
                    x, y, w, h = ss.window_in(ss.view(level, target), ss.view(level + part, target), cols, rows)
                    self.assertGreaterEqual(x, -1e-6, (target, level, part))
                    self.assertGreaterEqual(y, -1e-6, (target, level, part))
                    self.assertLessEqual(x + w, cols + 1e-6, (target, level, part))
                    self.assertLessEqual(y + h, rows + 1e-6, (target, level, part))

    def test_a_picture_seen_from_its_own_view_is_the_whole_picture(self):
        v = ss.view(7, ss.TARGETS[1])
        self.assertEqual(ss.window_in(v, v, 640, 360), (0.0, 0.0, 640.0, 360.0))
        self.assertEqual(ss.placed((0.0, 0.0, 640.0, 360.0), 640, 360, 1280, 720),
                         ((0.0, 0.0, 640.0, 360.0), (0.0, 0.0, 1280.0, 720.0)))

    def test_a_window_that_runs_off_the_picture_is_cut_down_and_so_is_its_place_on_screen(self):
        take, put = ss.placed((-64.0, 0.0, 640.0, 360.0), 640, 360, 1280, 720)
        self.assertEqual(take, (0.0, 0.0, 576.0, 360.0))
        self.assertEqual(put, (128.0, 0.0, 1152.0, 720.0))
        self.assertIsNone(ss.placed((700.0, 0.0, 100.0, 50.0), 640, 360, 1280, 720))
        self.assertIsNone(ss.placed((0.0, 0.0, 0.0, 0.0), 640, 360, 1280, 720))

    def test_the_zoom_is_steady_and_waits_one_step_past_the_newest_picture(self):
        self.assertAlmostEqual(ss.glide(3.0, ss.SECONDS_PER_LEVEL / 2, 3), 3.5)
        self.assertEqual(ss.glide(3.9, ss.SECONDS_PER_LEVEL, 3), 4.0, "it waits there for the next picture")
        self.assertEqual(ss.glide(4.0, 1.0, 3), 4.0)

    def test_every_target_is_on_the_edge_where_there_is_something_to_see(self):
        for target in ss.TARGETS:
            cx, cy, span = ss.view(12, target)
            counts = {int(n) for y in range(0, 24, 3) for n in ss.smooth_row(y, 32, 24, cx, cy, span, ss.limit_for(12))}
            self.assertGreater(len(counts), 6, target)

    def test_a_row_is_one_number_per_point_and_the_picture_is_symmetrical_on_the_real_axis(self):
        top = ss.smooth_row(2, 40, 30, -0.6, 0.0, 3.4, 60)
        bottom = ss.smooth_row(27, 40, 30, -0.6, 0.0, 3.4, 60)
        self.assertEqual(len(top), 40)
        self.assertEqual(top, bottom)

    def test_numpy_works_out_the_same_picture(self):
        np = ss.numpy_or_none()
        if np is None:
            self.skipTest("numpy is not installed")
        cx, cy, span = ss.view(9, ss.TARGETS[0])
        fast = ss.smooth_rows_np(np, 3, 9, 48, 27, cx, cy, span, 120)
        self.assertEqual(fast.shape, (6, 48))
        for i, y in enumerate(range(3, 9)):
            for got, want in zip(fast[i].tolist(), ss.smooth_row(y, 48, 27, cx, cy, span, 120)):
                self.assertAlmostEqual(got, want, places=9)

    def test_working_a_picture_out_covers_every_row_rests_between_strips_and_stops_when_told(self):
        np = ss.numpy_or_none()
        if np is None:
            self.skipTest("numpy is not installed")
        got, rests, clock = [], [], iter(range(1000))
        job = (16, 10, ss.view(2, ss.TARGETS[0]), 30)
        done = ss.work_rows(np, job, lambda y, numbers: got.append((y, len(numbers))), lambda: True, duty=0.25,
                            strip=4, clock=lambda: float(next(clock)), sleep=rests.append)
        self.assertTrue(done)
        self.assertEqual(got, [(0, 4), (4, 4), (8, 2)])
        self.assertEqual(rests, [3.0, 3.0], "each strip took 1 on this clock, so it rests 3: a quarter of the time")
        got = []
        done = ss.work_rows(np, job, lambda y, numbers: got.append(y), lambda: len(got) < 1, strip=4, sleep=lambda s: None)
        self.assertFalse(done)
        self.assertEqual(got, [0])

    def test_the_set_is_the_background_colour_and_the_rest_cycles_through_the_ramp(self):
        colours = ss.ramp("#000000", "#4c8dff", "#ff8a00")
        self.assertEqual(colours[0], "#000000")
        self.assertIn("#4c8dff", colours)
        self.assertIn("#ff8a00", colours)
        for colour in colours:
            self.assertRegex(colour, r"^#[0-9a-f]{6}$")
        size = len(ss.ramp("#000000", "#4c8dff", "#ff8a00", ss.PALETTE))
        self.assertEqual(size, ss.PALETTE + 1)
        self.assertEqual(ss.colour_index(-1.0, size), 0, "the set")
        self.assertEqual(ss.colour_index(0.0, size), 1)
        self.assertEqual(ss.colour_index((size - 1) / ss.DENSITY, size), 1, "it wraps round")
        self.assertTrue(all(1 <= ss.colour_index(n / 7.0, size) < size for n in range(5000)))
        self.assertEqual(ss.colour_words(["#000000", "#4c8dff"]), [0xff000000, 0xff4c8dff])

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


class WifiHeardTests(unittest.TestCase):
    def test_the_ordinary_scans_networks_are_heard_as_well_as_the_surveys(self):
        scan = [{"bssid": "AA:BB:CC:00:00:01", "signal_dbm": -60}, {"bssid": "aa:bb:cc:00:00:02", "signal_dbm": -75}]
        self.assertEqual(ss.wifi_heard(None, scan), [{"id": "aa:bb:cc:00:00:01", "rssi": -60},
                                                     {"id": "aa:bb:cc:00:00:02", "rssi": -75}])
        bands = ss.signal_bands({"wifi": ss.wifi_heard(None, scan)})
        self.assertEqual(bands[0]["caption"], "WI-FI  2 NETWORKS")

    def test_a_network_in_both_is_counted_once_with_the_surveys_reading(self):
        survey = [{"bssid": "aa:bb:cc:00:00:01", "signal": "-48 dBm"}]
        scan = [{"bssid": "AA:BB:CC:00:00:01", "signal_dbm": -60}, {"bssid": "aa:bb:cc:00:00:03", "signal_dbm": -80}]
        self.assertEqual(ss.wifi_heard(survey, scan), [{"id": "aa:bb:cc:00:00:01", "rssi": -48.0},
                                                       {"id": "aa:bb:cc:00:00:03", "rssi": -80}])

    def test_rows_without_a_reading_or_an_address_are_left_out(self):
        self.assertEqual(ss.wifi_heard([{"bssid": "x", "signal": ""}, {"signal": "-50 dBm"}, "junk"],
                                       [{"bssid": "y"}, {"bssid": "z", "signal_dbm": True}, None]), [])
        self.assertEqual(ss.wifi_heard(None, None), [])



if __name__ == "__main__":
    unittest.main()
