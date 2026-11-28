"""Closing the window stops a Ghost hunt recording, and does nothing about Ghost hunt when it is not installed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_gui import app, ghost  # noqa: E402


class GhostPage:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


class Win:
    def __init__(self, ghost_on, page):
        self.ghost_on = ghost_on
        self.pages = {ghost.TAB: page} if page is not None else {}


class GhostClosingTests(unittest.TestCase):
    def test_the_ghost_page_is_stopped_when_the_window_closes(self):
        page = GhostPage()
        listeners = app.closing_listeners(Win(True, page))
        self.assertEqual(listeners, [page])
        for listener in listeners:
            listener.stop()
        self.assertEqual(page.stopped, 1)

    def test_not_installed_means_nothing_about_it(self):
        self.assertEqual(app.closing_listeners(Win(False, GhostPage())), [])
        self.assertEqual(app.closing_listeners(Win(True, None)), [])

    def test_the_page_gives_its_stop_recording_as_stop(self):
        import inspect
        self.assertIn("page.stop = stop_recording", inspect.getsource(ghost.build))


if __name__ == "__main__":
    unittest.main()

