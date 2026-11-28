"""The Mandelbrot dive keeps a stop flag only for pictures whose threads may still be running."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_gui import screensaver as ss  # noqa: E402


class StopFlagTests(unittest.TestCase):
    def test_only_unfinished_pictures_keep_their_flags(self):
        a, b, c = threading.Event(), threading.Event(), threading.Event()
        pictures = [{"flag": a, "done": True}, {"flag": b, "done": False}, None]
        self.assertEqual(ss.flags_still_needed([a, b, c], pictures), [b])

    def test_a_long_dive_does_not_grow_the_list(self):
        flags, shown = [], None
        for _ in range(1000):                      # each new picture once the one before is finished
            work = {"flag": threading.Event(), "done": False}
            flags = ss.flags_still_needed(flags, (shown,)) + [work["flag"]]
            work["done"], shown = True, work
        self.assertEqual(len(flags), 1)

    def test_nothing_in_gives_nothing_out(self):
        self.assertEqual(ss.flags_still_needed(None, None), [])
        self.assertEqual(ss.flags_still_needed([threading.Event()], [{"done": False}]), [])


if __name__ == "__main__":
    unittest.main()

