import unittest

from ops_gui import foldertabs as f


class PaintOrderTests(unittest.TestCase):
    def test_the_selected_tab_is_drawn_last_and_the_rest_right_to_left(self):
        self.assertEqual(f.paint_order(5, 1), [4, 3, 2, 0, 1])
        self.assertEqual(f.paint_order(3, 0), [2, 1, 0])
        self.assertEqual(f.paint_order(3, 2), [1, 0, 2])

    def test_no_tabs_and_no_selection(self):
        self.assertEqual(f.paint_order(0, -1), [])
        self.assertEqual(f.paint_order(3, -1), [2, 1, 0])


class SeamTests(unittest.TestCase):
    """Where the pane leaves its top border out so the open tab and the pane are one surface."""

    def test_the_gap_is_the_inside_of_the_tab_between_its_walls(self):
        x, w = f.seam_span(100, 199, 0, 999)
        self.assertEqual(x, 100 + f.WALL_LEFT)                     # the left wall still meets the border
        self.assertEqual(x + w - 1, 199 + f.OVERLAP - f.WALL_RIGHT)   # and so does the foot's

    def test_a_tab_scrolled_partly_out_of_view_only_opens_what_shows(self):
        self.assertEqual(f.seam_span(-40, 59, 0, 999)[0], 0)
        x, w = f.seam_span(950, 1049, 0, 999)
        self.assertEqual(x + w - 1, 999)

    def test_no_gap_for_a_tab_that_is_wholly_out_of_view(self):
        self.assertIsNone(f.seam_span(1100, 1199, 0, 999))
        self.assertIsNone(f.seam_span(-300, -200, 0, 999))


class OneSheetOfGlassTests(unittest.TestCase):
    """Tabs and pane are one glass layer; only what sits inside the pane may be a second."""

    def test_tab_images_are_solid_because_the_tab_bar_makes_them_glass_in_one_step(self):
        from ops_gui import skin
        for name in ("tab_off", "tab_hover", "tab_on"):
            self.assertFalse(skin.SPECS[name][5].get("glass"), name)
        for name in ("pane", "card", "row"):
            self.assertTrue(skin.SPECS[name][5].get("glass"), name)

    def test_no_tab_overlaps_the_pane(self):
        from ops_gui import skin, style
        for mode in ("dark", "light"):
            sheet = style.stylesheet(mode, {n: f"/tmp/{n}.png" for n in skin.SPECS})
            pane = [line for line in sheet.splitlines() if line.startswith("QTabWidget::pane")][0]
            self.assertIn("top: 0px", pane)
            for line in sheet.splitlines():
                if line.startswith("QTabBar::tab"):
                    margin = line.split("margin:")[1].split(";")[0].split() if "margin:" in line else []
                    if len(margin) == 4:
                        self.assertFalse(margin[2].startswith("-"), line)   # top right BOTTOM left


class DragTests(unittest.TestCase):
    """Dragging the tab bar sideways when there are more tabs than fit."""

    def test_a_tab_is_scrolled_for_every_step_of_drag(self):
        self.assertEqual(f.drag_steps(0), 0)
        self.assertEqual(f.drag_steps(-(f.DRAG_STEP - 1)), 0)
        self.assertEqual(f.drag_steps(-f.DRAG_STEP), 1)
        self.assertEqual(f.drag_steps(-3 * f.DRAG_STEP - 5), 3)

    def test_dragging_left_brings_later_tabs_in_and_right_earlier_ones(self):
        self.assertGreater(f.drag_steps(-200), 0)
        self.assertLess(f.drag_steps(200), 0)
        self.assertEqual(f.drag_steps(200), -f.drag_steps(-200))

    def test_a_tap_that_wobbles_is_not_a_drag(self):
        self.assertLess(f.DRAG_START, f.DRAG_STEP)
        self.assertEqual(f.drag_steps(-(f.DRAG_START + 1)), 0)

    def test_running_out_of_tabs_resets_where_the_drag_counts_from(self):
        for x, done in ((300, 2), (40, -1), (500, 0)):
            self.assertEqual(f.drag_steps(x - f.reanchor(x, done)), done)
            self.assertEqual(f.drag_steps(x - f.DRAG_STEP - f.reanchor(x, done)), done + 1)   # and back at once


class ArrowTabTests(unittest.TestCase):
    """The scroll arrows are small tabs of their own at each end; no tab is ever drawn under one."""

    def test_the_tabs_keep_clear_of_an_arrow_at_each_end(self):
        lo, hi = f.tab_zone(1086, 36)
        self.assertEqual(lo, 36)                         # the left arrow has columns 0 to 35
        self.assertEqual(hi, 1086 - 36 - 1)              # and the right arrow the last 36
        self.assertEqual(lo, 1086 - 1 - hi)              # the same room at both ends

    def test_a_bar_too_narrow_for_anything_but_arrows_has_no_room_for_tabs(self):
        lo, hi = f.tab_zone(60, 36)
        self.assertLessEqual(hi, lo)

    def test_the_arrow_heads_point_the_right_way_and_mirror_each_other(self):
        left, right = f.arrow_cells("left"), f.arrow_cells("right")
        tip = lambda cells: [x for x, y, w, h in cells if h == 1][0]
        base = lambda cells: [x for x, y, w, h in cells if h == 7][0]
        self.assertLess(tip(left), base(left))
        self.assertGreater(tip(right), base(right))
        self.assertEqual(sorted((3 - x, y, w, h) for x, y, w, h in left), sorted(right))
        for x, y, w, h in left + right:
            self.assertTrue(0 <= x <= 3 and 0 <= y and y + h <= 7)
            self.assertEqual(y + h - 1, 6 - y)             # each column is centred top to bottom


if __name__ == "__main__":
    unittest.main()
