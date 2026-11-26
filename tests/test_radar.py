"""The two flat views of the Sense page: the radar's arcs and the view from above."""
import inspect
import math
import unittest

from ops_gui import radar as r, room3d, sense


def nodes(db=0.0, names=("c", "a", "b"), **more):
    return [dict({"name": n, "db": db, "dbm": -60.0}, **more) for n in names]


def flat_views():
    """The source of the radar and the view from above, with the helpers they share."""
    src = inspect.getsource(sense)
    return src[src.index("    def link_pen("):src.index("    class Page(QWidget):")]


class ArcTests(unittest.TestCase):
    def test_senders_keep_their_place_however_loud(self):
        key = lambda segs: [(s["name"], round(s["a0"], 6), round(s["a1"], 6), round(s["r"], 6)) for s in segs]
        self.assertEqual(key(r.segments(nodes(0.0))), key(r.segments(nodes(18.0))))
        self.assertEqual([s["name"] for s in r.segments(nodes())], ["a", "b", "c"])

    def test_each_sender_gets_an_equal_share_of_the_circle_with_a_gap_between(self):
        segs = r.segments(nodes())
        for s in segs:
            self.assertAlmostEqual(s["a1"] - s["a0"], r.TAU / 3 * r.SHARE)
            self.assertAlmostEqual((s["a0"] + s["a1"]) / 2, s["mid"])
        mids = [s["mid"] for s in segs]
        for a, b in zip(mids, mids[1:]):
            self.assertAlmostEqual(b - a, r.TAU / 3)
        for a, b in zip(segs, segs[1:]):
            self.assertGreater(b["a0"], a["a1"])                         # arcs never touch or overlap

    def test_one_sender_alone_gets_the_whole_ring_so_it_has_no_side_at_all(self):
        seg = r.segments(nodes(names=("only",)))[0]
        self.assertAlmostEqual(seg["a1"] - seg["a0"], r.TAU)

    def test_an_arc_is_never_small_enough_to_look_like_a_dot(self):
        for count in range(1, 7):                                        # the page follows at most a handful
            for s in r.segments(nodes(names=[str(i) for i in range(count)])):
                self.assertGreater(s["a1"] - s["a0"], math.radians(40))

    def test_distance_is_signal_strength(self):
        near = r.segments([{"name": "a", "db": 0, "dbm": -40.0}])[0]["r"]
        far = r.segments([{"name": "a", "db": 0, "dbm": -85.0}])[0]["r"]
        self.assertLess(near, far)
        self.assertTrue(0.3 <= near <= 1.0 and 0.3 <= far <= 1.0)
        self.assertEqual(r.segments([{"name": "a", "db": 0, "dbm": None}])[0]["r"], 0.65)

    def test_empty_and_bad_values(self):
        self.assertEqual(r.segments([]), [])
        segs = r.segments([{"name": "a", "db": None, "dbm": None}, {"name": "b", "db": -4.0, "trail": None}])
        self.assertTrue(all(s["db"] == 0.0 and s["trail"] == 0.0 and s["sure"] for s in segs))

    def test_arcs_carry_whether_they_can_be_trusted_and_their_trail(self):
        segs = r.segments([{"name": "a", "db": 2.0, "sure": False, "trail": 7.0}, {"name": "b", "db": 2.0}])
        self.assertEqual([(s["sure"], s["trail"]) for s in segs], [(False, 7.0), (True, 0.0)])

    def test_an_arc_takes_the_same_look_as_a_line_in_the_room(self):
        # nothing here decides quiet, subtle, strong or not sure: the room's own function does, for all views
        self.assertNotIn("LEVELS", inspect.getsource(r))
        for name in ("link_look", "WIDTHS", "trail_db"):
            self.assertFalse(hasattr(r, name), name)
        views = flat_views()
        self.assertEqual(views.count('room3d.link_look(s["db"], s["trail"], s["sure"])'), 1)
        self.assertEqual(views.count('room3d.link_look(q["db"], q["trail"], q["sure"])'), 1)
        self.assertEqual(views.count("p.setPen(link_pen(look, col, mode))"), 2)


class DrawingTests(unittest.TestCase):
    def test_rings_are_integer_points_on_the_right_radius(self):
        pts = r.ring_points(100, 80, 50)
        self.assertEqual(len(pts), 73)
        self.assertEqual(pts[0], pts[-1])
        self.assertTrue(all(isinstance(x, int) and isinstance(y, int) for x, y in pts))
        for x, y in pts:
            self.assertAlmostEqual(math.hypot(x - 100, y - 80), 50, delta=0.8)

    def test_an_arc_runs_from_its_start_to_its_end_on_its_radius(self):
        pts = r.arc_points(100, 80, 50, 0.0, math.pi / 2)
        self.assertEqual(pts[0], (150, 80))
        self.assertEqual(pts[-1], (100, 130))
        self.assertGreaterEqual(len(pts), 10)
        for x, y in pts:
            self.assertAlmostEqual(math.hypot(x - 100, y - 80), 50, delta=0.8)
        self.assertEqual(len(r.arc_points(0, 0, 10, 1.0, 1.0)), 3)        # never fewer than a drawable line

    def test_marks_only_sit_on_a_disturbed_trusted_arc(self):
        seg = lambda db, sure=True: r.segments(nodes(db, names=("a", "b"), sure=sure))[0]
        self.assertEqual(r.arc_ticks(seg(0.0), 3, 100, 100, 80), [])
        self.assertEqual(r.arc_ticks(seg(2.9), 3, 100, 100, 80), [])
        self.assertEqual(r.arc_ticks(seg(15.0, sure=False), 3, 100, 100, 80), [])
        self.assertEqual([len(r.arc_ticks(seg(v), 0, 100, 100, 80)) for v in room3d.LEVELS], [3, 4, 5, 6])

    def test_marks_cover_the_whole_arc_evenly_never_one_spot(self):
        seg = r.segments(nodes(12.0))[1]
        ticks = r.arc_ticks(seg, 0, 0, 0, 100)
        angles = [math.atan2((a[1] + b[1]) / 2, (a[0] + b[0]) / 2) for a, b in ticks]
        gaps = [(y - x) % r.TAU for x, y in zip(angles, angles[1:])]
        for g in gaps:
            self.assertAlmostEqual(g, gaps[0], delta=0.03)
        self.assertGreater(sum(gaps), (seg["a1"] - seg["a0"]) * 0.7)
        for a, b in ticks:                                               # each mark lies across the arc, on it
            self.assertAlmostEqual(math.hypot((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), 100 * seg["r"], delta=1.0)

    def test_marks_beat_with_each_reading_and_never_travel_round_the_circle(self):
        seg = r.segments(nodes(12.0))[0]
        mids = lambda tick: [(round((a[0] + b[0]) / 2), round((a[1] + b[1]) / 2)) for a, b in r.arc_ticks(seg, tick, 0, 0, 100)]
        size = lambda tick: math.hypot(*[q - p for p, q in zip(*r.arc_ticks(seg, tick, 0, 0, 100)[0])])
        self.assertEqual(r.arc_ticks(seg, 5, 0, 0, 100), r.arc_ticks(seg, 5, 0, 0, 100))     # no clock in it
        for tick in range(1, 6):
            for p, q in zip(mids(0), mids(tick)):
                self.assertLessEqual(abs(p[0] - q[0]) + abs(p[1] - q[1]), 2)
        self.assertNotEqual(size(0), size(1))
        self.assertEqual(r.arc_ticks(seg, 0, 0, 0, 100), r.arc_ticks(seg, 2, 0, 0, 100))

    def test_a_name_sits_just_outside_the_middle_of_its_arc(self):
        seg = r.segments(nodes())[0]                                     # the first slot is drawn at the top
        x, y = r.label_spot(seg, 200, 150, 100)
        self.assertEqual(x, 200)
        self.assertEqual(y, round(150 - (100 * seg["r"] + 12)))


class FromAboveTests(unittest.TestCase):
    def test_it_is_the_rooms_own_layout_looked_at_straight_down(self):
        scale, cx, cy = r.plan_fit(400, 316)
        self.assertEqual((cx, cy), (200.0, 150.0))
        self.assertEqual(r.plan_point(0, 0, scale, cx, cy), (200, 150))
        post = room3d.layout(nodes())[0]
        x, y = r.plan_point(post["x"], post["z"], scale, cx, cy)
        self.assertAlmostEqual(math.hypot(x - 200, y - 150), math.hypot(post["x"], post["z"]) * scale, delta=1.0)
        self.assertNotIn("def layout", inspect.getsource(r))             # no second set of slots

    def test_the_floor_fits_the_widget_whatever_its_shape(self):
        for w, h in ((400, 316), (700, 300), (240, 230), (10, 10)):
            scale, cx, cy = r.plan_fit(w, h)
            self.assertGreaterEqual(scale, 1.0)
            if min(w, h) > 100:
                x0, y0 = r.plan_point(-room3d.FLOOR, -room3d.FLOOR, scale, cx, cy)
                x1, y1 = r.plan_point(room3d.FLOOR, room3d.FLOOR, scale, cx, cy)
                self.assertTrue(0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h - 16, (w, h))

    def test_the_floor_is_a_plain_grid(self):
        lines = r.plan_grid(50.0, 200.0, 150.0)
        self.assertEqual(len(lines), 2 * (room3d.CELLS + 1))
        for (ax, ay), (bx, by) in lines:
            self.assertTrue(ax == bx or ay == by)
        views = flat_views()
        above = views[views.index("    class Ring(QWidget):"):]
        self.assertEqual(above.count('QColor(col["floor"])'), 1)          # the one colour the floor is ever given

    def test_a_sender_square_never_lights_up_only_its_line_does(self):
        views = flat_views()
        above = views[views.index("    class Ring(QWidget):"):]
        self.assertEqual(above.count("heat_colour"), 0)                  # all heat comes through link_pen
        self.assertIn('p.drawLine(middle, P(q["x"], q["z"]))', above)
        self.assertIn("room3d.ripple_bars(q, self.tick)", above)


class HonestyTests(unittest.TestCase):
    def test_the_sweep_and_the_flaring_blips_are_gone(self):
        for name in ("blips", "glow", "fan_rays", "blip_size", "FAN", "GLOW_SPAN"):
            self.assertFalse(hasattr(r, name), name)
        views = flat_views()
        for gone in ("self.angle", "self.timer", "showEvent", "drawEllipse", "self.dbs"):
            self.assertNotIn(gone, views)

    def test_the_flat_views_have_no_timer_and_no_clock(self):
        views = flat_views()
        self.assertNotIn("QTimer", views)
        self.assertNotIn("time.time", views)
        self.assertEqual(views.count("self.update()"), 2)                # once in each view, when a reading arrives
        self.assertEqual(views.count("def set_nodes(self, nodes, state):"), 2)

    def test_every_view_is_given_every_reading(self):
        src = inspect.getsource(sense)
        for view in ("room", "ring", "radar"):
            self.assertEqual(src.count(f"self.{view}.set_nodes(nodes, e.state)"), 1, view)

    def test_the_flat_views_take_every_colour_from_the_theme(self):
        views = flat_views()
        self.assertNotRegex(views, r"#[0-9a-fA-F]{3,6}")
        self.assertNotIn('TOKENS["dark"]', views)
        self.assertEqual(views.count("col = room3d.colours(mode)"), 2)
        self.assertEqual(views.count("mode = theme()"), 2)

    def test_both_views_say_that_a_place_on_screen_means_nothing(self):
        for caption in (sense.RADAR_CAPTION, sense.ABOVE_CAPTION):
            self.assertEqual(caption[0], "not a map: position on screen means nothing")
            self.assertIn("nearer the centre = stronger signal", caption[1])
        views = flat_views()
        self.assertIn("flat_caption(p, fm, col, w, h, RADAR_CAPTION)", views)
        self.assertIn("flat_caption(p, fm, col, w, h, ABOVE_CAPTION)", views)
        self.assertIn("has no direction", inspect.getsource(r))

    def test_a_caption_goes_on_two_lines_when_it_is_too_wide(self):
        self.assertEqual(r.caption_lines(("ab", "cd"), 100, len), ["ab; cd"])
        self.assertEqual(r.caption_lines(("ab", "cd"), 5, len), ["ab", "cd"])

    def test_the_sentence_strips_and_pulse_sit_outside_the_switched_views(self):
        src = inspect.getsource(sense)
        page = src[src.index("    class Page(QWidget):"):]
        for shared in ("hl2.addWidget(self.line)", "hl2.addWidget(self.strips)", "row.addWidget(self.pulse)"):
            self.assertEqual(page.count(shared), 1, shared)
        self.assertEqual(page.count("self.stack.addWidget("), 3)         # only the three pictures are switched
        for inside in ("self.line", "self.strips", "self.pulse"):
            self.assertNotIn(f"self.stack.addWidget({inside})", page)


if __name__ == "__main__":
    unittest.main()
