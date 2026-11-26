import unittest

from ops_gui import slideswitch as s


def colours_at(cells, x, y):
    """Every colour drawn over grid cell (x, y), first to last."""
    return [c for cx, cy, w, h, c in cells if cx <= x < cx + w and cy <= y < cy + h]


class SlideSwitchTests(unittest.TestCase):
    def test_it_is_the_size_the_overview_switch_always_was(self):
        self.assertEqual((s.WIDTH, s.HEIGHT), (76, 36))

    def test_every_cell_stays_inside_the_switch(self):
        for on in (True, False):
            for x, y, w, h, _ in s.cells(on):
                self.assertTrue(0 <= x and x + w <= s.GRID_W and 0 <= y and y + h <= s.GRID_H)

    def test_on_is_green_with_the_knob_on_the_right(self):
        cells = s.cells(True)
        self.assertEqual(colours_at(cells, 8, 9)[0], s.ON[0])                 # green track on the left
        self.assertEqual(colours_at(cells, 30, 9)[-1], "#f2eef5")             # the knob's face on the right
        self.assertIn(s.MARK_ON, colours_at(cells, 12, 8))                    # the I mark

    def test_off_is_dark_with_the_knob_on_the_left_and_an_o_on_the_right(self):
        cells = s.cells(False)
        self.assertEqual(colours_at(cells, 8, 9)[-1], "#f2eef5")
        self.assertEqual(colours_at(cells, 30, 9), [s.OFF[0]])
        self.assertIn(s.MARK_OFF, colours_at(cells, 23, 8))

    def test_the_corners_are_cut(self):
        for on in (True, False):
            self.assertEqual(colours_at(s.cells(on), 0, 0), [])
            self.assertEqual(colours_at(s.cells(on), s.GRID_W - 1, s.GRID_H - 1), [])

    def test_a_busy_switch_is_amber_whichever_way_it_is(self):
        self.assertEqual(colours_at(s.cells(True, busy=True), 8, 9)[0], s.BUSY[0])
        self.assertEqual(colours_at(s.cells(False, busy=True), 30, 9), [s.BUSY[0]])

    def test_the_knob_travels_between_its_two_ends_and_no_further(self):
        self.assertEqual(s.knob_x(0), s.KNOB_LEFT)
        self.assertEqual(s.knob_x(1), s.KNOB_LEFT + s.KNOB_TRAVEL)
        self.assertEqual(s.knob_x(0.5), s.KNOB_LEFT + s.KNOB_TRAVEL // 2)
        self.assertEqual(s.knob_x(7), s.knob_x(1))
        self.assertEqual(s.knob_x(-1), s.knob_x(0))

    def test_a_pressed_or_disabled_knob_is_one_flat_colour(self):
        for kw, colour in (({"down": True}, "#d9d2e0"), ({"enabled": False}, "#8b93a1")):
            knob = {c for x, y, w, h, c in s.cells(False, **kw)[-len([ch for row in s.KNOB for ch in row if ch != "."]):]}
            self.assertEqual(knob, {colour})


if __name__ == "__main__":
    unittest.main()
