"""
Moving round the app with the D-pad: the geometry and the rules, with no screen.

    python3 -m unittest discover -s tests
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from ops_gui import focusnav as f  # noqa: E402
from ops_gui import navring  # noqa: E402

# A window like the app's: a row of tabs across the top, two rows of controls below and one at the side.
TABS = ("tabs", (0, 0, 1200, 50))
A = ("a", (20, 80, 200, 40))
B = ("b", (240, 80, 200, 40))
C = ("c", (20, 140, 200, 40))
D = ("d", (240, 140, 200, 40))
WIDE = ("list", (20, 200, 800, 300))
SIDE = ("side", (900, 210, 200, 40))
ALL = [TABS, A, B, C, D, WIDE, SIDE]


def go(start, direction, among=ALL):
    rect = dict(among)[start]
    return f.next_in_direction(rect, [(k, r) for k, r in among if k != start], direction)


class DirectionTests(unittest.TestCase):
    def test_the_highlight_goes_where_the_arrow_points(self):
        self.assertEqual(go("a", "right"), "b")
        self.assertEqual(go("b", "left"), "a")
        self.assertEqual(go("a", "down"), "c")
        self.assertEqual(go("c", "up"), "a")
        self.assertEqual(go("d", "up"), "b")

    def test_up_from_the_top_row_reaches_the_tabs_and_nothing_is_above_them(self):
        self.assertEqual(go("a", "up"), "tabs")
        self.assertEqual(go("b", "up"), "tabs")
        self.assertIsNone(go("tabs", "up"))
        self.assertIsNone(go("tabs", "left"))
        self.assertIsNone(go("tabs", "right"))

    def test_straight_ahead_beats_nearer_but_off_to_the_side(self):
        self.assertEqual(go("c", "down"), "list")
        self.assertEqual(go("d", "down"), "list")
        self.assertEqual(go("list", "right"), "side")
        self.assertEqual(go("side", "left"), "list")
        among = [("here", (0, 0, 100, 40)), ("below", (0, 160, 100, 40)), ("beside-near", (150, 60, 100, 40))]
        self.assertEqual(go("here", "down", among), "below", "120 straight down beats 20 down and 50 across")
        far = [("here", (0, 0, 100, 40)), ("below-far", (0, 400, 100, 40)), ("beside-near", (150, 60, 100, 40))]
        self.assertEqual(go("here", "down", far), "beside-near", "but not something a whole screen away")

    def test_a_control_that_is_not_that_way_is_never_chosen(self):
        self.assertIsNone(go("b", "right", [A, B, C, D]))
        self.assertIsNone(go("c", "left", [A, B, C, D]))
        self.assertIsNone(go("d", "down", [A, B, C, D]))
        self.assertIsNone(f.distance((0, 0, 10, 10), (0, 0, 10, 10), "down"), "a control on top of this one")
        self.assertIsNone(f.distance((0, 0, 10, 10), (50, 50, 10, 10), "sideways"))

    def test_every_control_can_be_reached_from_the_tabs_and_the_tabs_from_every_control(self):
        """The promise made to the person holding the D-pad, checked on this layout: nothing is cut off."""
        def reach(start):
            seen, todo = {start}, [start]
            while todo:
                here = todo.pop()
                for direction in f.DIRECTIONS:
                    there = go(here, direction)
                    if there is not None and there not in seen:
                        seen.add(there)
                        todo.append(there)
            return seen
        # down from the tabs is first_in_reading_order in the app, so start the walk from that control too
        first = f.first_in_reading_order([kr for kr in ALL if kr[0] != "tabs"])
        self.assertEqual(first, "a")
        self.assertEqual(reach(first), {k for k, _ in ALL})
        for key, _ in ALL:
            self.assertIn("tabs", reach(key), key)

    def test_down_from_the_tabs_starts_at_the_top_left_of_the_page(self):
        self.assertEqual(f.first_in_reading_order([D, C, B, A, WIDE]), "a")
        self.assertEqual(f.first_in_reading_order([("low", (0, 300, 50, 40)), ("right", (400, 10, 50, 40)),
                                                   ("left-a-little-lower", (10, 22, 50, 40))]),
                         "left-a-little-lower", "the same row, read left to right")
        self.assertIsNone(f.first_in_reading_order([]))


class RuleTests(unittest.TestCase):
    def test_a_list_keeps_up_and_down_until_its_first_or_last_row(self):
        self.assertTrue(f.passes_through("list", "down", at_start=True, at_end=False))
        self.assertTrue(f.passes_through("list", "up", at_start=False, at_end=True))
        self.assertFalse(f.passes_through("list", "up", at_start=True, at_end=False), "free to leave upward")
        self.assertFalse(f.passes_through("list", "down", at_start=False, at_end=True), "free to leave downward")
        self.assertFalse(f.passes_through("list", "left", False, False))
        self.assertFalse(f.passes_through("list", "right", False, False))

    def test_a_page_of_text_scrolls_before_the_highlight_leaves_it(self):
        self.assertTrue(f.passes_through("text", "down", at_start=True, at_end=False))
        self.assertFalse(f.passes_through("text", "down", at_start=False, at_end=True))
        self.assertFalse(f.passes_through("text", "up", at_start=True, at_end=True))

    def test_tabs_sliders_and_typing_keep_left_and_right(self):
        for kind in ("slider", "line"):
            for direction in ("left", "right"):
                self.assertTrue(f.passes_through(kind, direction), (kind, direction))
            for direction in ("up", "down"):
                self.assertFalse(f.passes_through(kind, direction), (kind, direction))

    def test_a_row_of_tabs_keeps_left_and_right_until_its_first_or_last_tab(self):
        self.assertTrue(f.passes_through("tabs", "right", at_start=True, at_end=False))
        self.assertTrue(f.passes_through("tabs", "left", at_start=False, at_end=True))
        self.assertFalse(f.passes_through("tabs", "right", at_start=False, at_end=True),
                         "past the last tab: on to the buttons beside the row")
        self.assertFalse(f.passes_through("tabs", "left", at_start=True, at_end=False))
        for direction in ("up", "down"):
            self.assertFalse(f.passes_through("tabs", direction, False, False))
        self.assertTrue(f.passes_through("spin", "up"))
        self.assertFalse(f.passes_through("spin", "left"))

    def test_a_button_keeps_no_arrow_at_all(self):
        for direction in f.DIRECTIONS:
            self.assertFalse(f.passes_through("other", direction))
            self.assertFalse(f.passes_through("combo", direction))

    def test_back_goes_one_level_at_a_time(self):
        self.assertEqual(f.back_step(True, True, True, True, False), "drawer")
        self.assertEqual(f.back_step(False, True, True, True, False), "reading")
        self.assertEqual(f.back_step(False, False, True, True, False), "tabs")
        self.assertEqual(f.back_step(False, False, False, True, False), "window")
        self.assertEqual(f.back_step(False, False, False, False, False), "first")
        self.assertIsNone(f.back_step(False, False, False, False, True))

    def test_the_apps_own_hand_drawn_controls_are_found_and_qts_are_not(self):
        Drawn = type("Drawn", (object,), {"mousePressEvent": lambda self, e: None, "__module__": "ops_gui.home_panel"})
        Child = type("Child", (Drawn,), {"__module__": "ops_gui.other"})
        Plain = type("Plain", (object,), {"__module__": "ops_gui.other"})
        Qt = type("QPushButton", (object,), {"mousePressEvent": lambda self, e: None, "__module__": "PyQt6.QtWidgets"})
        self.assertTrue(f.custom_clickable(Drawn))
        self.assertTrue(f.custom_clickable(Child), "inherited from one of the app's own")
        self.assertFalse(f.custom_clickable(Plain))
        self.assertFalse(f.custom_clickable(Qt))


class RingTests(unittest.TestCase):
    def test_the_frame_is_only_the_edge_so_clicks_pass_through_the_middle(self):
        parts = navring.frame_region(100, 40, 3)
        self.assertEqual(sum(w * h for _, _, w, h in parts), 100 * 40 - 94 * 34)
        covered = lambda x, y: any(px <= x < px + w and py <= y < py + h for px, py, w, h in parts)
        self.assertFalse(covered(50, 20), "the middle is open")
        for x, y in ((0, 0), (99, 39), (0, 20), (99, 20), (50, 0), (50, 39)):
            self.assertTrue(covered(x, y), (x, y))
        self.assertEqual(len(navring.frame_region(4, 4, 3)), 4, "a tiny control still gets a frame")


class TypingTests(unittest.TestCase):
    """A stray arrow key must not take the highlight out of a box someone is typing in (typing_keeps)."""

    def setUp(self):
        from ops_gui import focusnav
        self.keeps = focusnav.typing_keeps

    def test_a_box_with_text_keeps_the_keyboards_up_and_down(self):
        self.assertTrue(self.keeps("line", "up", True, "good mor"))
        self.assertTrue(self.keeps("line", "down", True, "good mor"))

    def test_an_empty_box_lets_the_keyboard_leave(self):
        self.assertFalse(self.keeps("line", "up", True, ""))
        self.assertFalse(self.keeps("line", "down", True, ""))

    def test_the_terminals_box_always_keeps_them_for_its_history(self):
        self.assertTrue(self.keeps("history", "up", True, ""))
        self.assertTrue(self.keeps("history", "down", True, "ls"))

    def test_the_gamepad_can_always_move_on(self):
        for kind in ("line", "history"):
            for direction in ("up", "down"):
                self.assertFalse(self.keeps(kind, direction, False, "typed"))

    def test_left_and_right_and_other_controls_are_not_its_business(self):
        self.assertFalse(self.keeps("line", "left", True, "typed"))
        self.assertFalse(self.keeps("history", "right", True, "typed"))
        self.assertFalse(self.keeps("list", "up", True, "typed"))


if __name__ == "__main__":
    unittest.main()

