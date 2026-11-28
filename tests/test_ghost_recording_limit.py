"""A Ghost hunt recording stops by itself after four hours or at 20 MB, whichever comes first."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_gui import ghost  # noqa: E402

HOURS4, MB20 = 4 * 3600, 20 * 1000 * 1000


class RecordingLimitTests(unittest.TestCase):
    def test_the_limits_are_four_hours_and_20_mb(self):
        self.assertEqual(ghost.RECORD_MOST_SECONDS, HOURS4)
        self.assertEqual(ghost.RECORD_MOST_BYTES, MB20)

    def test_just_under_four_hours_goes_on_and_four_hours_stops(self):
        self.assertEqual(ghost.recording_limit(1000.0, 1000.0 + HOURS4 - 0.1, 100, 100), "")
        why = ghost.recording_limit(1000.0, 1000.0 + HOURS4, 100, 100)
        self.assertIn("four hours", why)

    def test_a_line_that_fits_exactly_is_written_and_one_byte_more_stops(self):
        self.assertEqual(ghost.recording_limit(0.0, 10.0, MB20 - 300, 300), "")
        self.assertIn("20 MB", ghost.recording_limit(0.0, 10.0, MB20 - 300, 301))

    def test_whichever_comes_first(self):
        self.assertIn("four hours", ghost.recording_limit(0.0, HOURS4 + 1, MB20 + 1, 10))
        self.assertIn("20 MB", ghost.recording_limit(0.0, 60.0, MB20, 1))

    def test_a_clock_set_back_does_not_stop_it(self):
        self.assertEqual(ghost.recording_limit(5000.0, 10.0, 100, 100), "")

    def test_the_words_say_plainly_why(self):
        for why in (ghost.recording_limit(0.0, HOURS4, 0), ghost.recording_limit(0.0, 1.0, MB20, 1)):
            self.assertTrue(why.startswith("Recording stopped by itself"))


if __name__ == "__main__":
    unittest.main()

