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


class LeavingTests(unittest.TestCase):
    """How the program ends once the window has closed (app.finish). No QApplication is made here."""

    def setUp(self):
        from ops_gui import app
        self.app = app

    def test_what_was_printed_is_flushed_and_then_the_program_leaves_with_its_code(self):
        did = []

        class Stream:
            def __init__(self, name):
                self.name = name

            def flush(self):
                did.append("flush " + self.name)
        self.app.finish(3, streams=[Stream("out"), Stream("err")], leave=lambda code: did.append(("leave", code)))
        self.assertEqual(did, ["flush out", "flush err", ("leave", 3)])

    def test_the_jobs_registered_for_the_end_of_python_are_not_run(self):
        # PyQt registers one, and running it is what crashed; see finish().
        import atexit
        ran = []
        atexit.register(ran.append, "registered job")
        try:
            self.app.finish(0, streams=[], leave=lambda code: None)
        finally:
            atexit.unregister(ran.append)
        self.assertEqual(ran, [])

    def test_a_broken_stream_does_not_stop_it_leaving(self):
        left = []

        class Broken:
            def flush(self):
                raise BrokenPipeError()
        self.app.finish(None, streams=[Broken()], leave=left.append)
        self.assertEqual(left, [0])

    def test_main_ends_through_finish_and_never_returns_into_teardown(self):
        import inspect
        source = inspect.getsource(self.app.main)
        self.assertIn("finish(app.exec())", source)
        self.assertNotIn("return app.exec()", source)


class GamepadClosingTests(unittest.TestCase):
    def test_the_gamepads_watcher_is_stopped_before_its_file_is_closed(self):
        # Closing the file first made Qt print "QSocketNotifier: Invalid socket" on every exit.
        import inspect
        from ops_gui import app
        source = inspect.getsource(app)
        closing = source[source.index("def closeEvent"):]
        stop, close = closing.index("self.pad_notifier.setEnabled(False)"), closing.index("os.close(self.pad_fd)")
        self.assertLess(stop, close)


if __name__ == "__main__":
    unittest.main()
