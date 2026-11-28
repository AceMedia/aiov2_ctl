"""Replaying a session file someone has edited by hand: a line whose time is not a usable time is left out, so
the replay line (and the tab's timer that redraws it every second) never fails on it."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_gui import ghost  # noqa: E402


def line(t, elapsed):
    return '{"bands": [], "elapsed": %s, "instruments": [], "time": %s}' % (elapsed, t)


class EditedSessionTests(unittest.TestCase):
    def test_lines_with_impossible_times_are_left_out_and_the_rest_replay(self):
        text = "\n".join([ghost.header_line(1764000000.0), line(1764000002.0, 2.0), line("1e20", 4.0),
                          line("NaN", 6.0), line(1764000008.0, "Infinity")]) + "\n"
        _head, records = ghost.parse_session(text)
        self.assertEqual([r["elapsed"] for r in records], [2.0])
        for r in records:
            ghost.replay_mark(_head, r)          # must not raise


if __name__ == "__main__":
    unittest.main()

