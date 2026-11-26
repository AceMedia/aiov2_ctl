import unittest

from ops_gui import app


class Timer:
    def __init__(self, active):
        self.active = active

    def isActive(self):
        return self.active


class Page:
    def __init__(self, feed=None, waiting=0, timer=None):
        self.feed, self.waiting, self.timer = feed, waiting, timer


class Wifi:
    def __init__(self, survey, sense):
        self.survey, self.sense = survey, sense


class Win:
    def __init__(self, bt, survey, sense):
        self.pages, self.wifi_panel = {"Bluetooth": bt}, Wifi(survey, sense)


class ClosingTests(unittest.TestCase):
    """Closing the window stops whatever is listening, so it saves and frees its radio."""

    def test_only_pages_that_are_listening_are_stopped(self):
        bt, survey, sense = Page(feed=object()), Page(), Page(timer=Timer(False))
        self.assertEqual(app.closing_listeners(Win(bt, survey, sense)), [bt])

    def test_a_scan_waiting_for_its_radio_and_a_watch_on_its_timer_both_count(self):
        bt, survey, sense = Page(waiting=123.0), Page(feed=object()), Page(timer=Timer(True))
        self.assertEqual(app.closing_listeners(Win(bt, survey, sense)), [bt, survey, sense])

    def test_a_window_without_those_pages_is_fine(self):
        class Bare:
            pages = {}
        self.assertEqual(app.closing_listeners(Bare()), [])


if __name__ == "__main__":
    unittest.main()
