"""The fixes from the outside review: a refresh that is not dropped, a position dated by the receiver, a scan
whose end is only judged once everything it printed is in, and a field log that says when it is not being written."""
import errno
import io
import json
import os
import tempfile
import threading
import time
import unittest

from ops_gui import app, bt_panel as bt, fieldlog as fl, position as p, sense as s

T0 = 1795600000.0                      # some moment; the receiver's stamps below are built from it


def stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def fix(t=None, **more):
    out = {"class": "TPV", "mode": 3, "lat": 51.5, "lon": -0.12}
    if t is not None:
        out["time"] = stamp(t)
    out.update(more)
    return out


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


class FollowUpTests(unittest.TestCase):
    def test_nothing_remembered_means_nothing_to_run(self):
        self.assertFalse(app.FollowUp().take())

    def test_a_request_made_while_busy_is_run_once_when_the_work_ends(self):
        f = app.FollowUp()
        f.remember()
        f.remember()                                        # a burst of switches is still one more refresh
        self.assertTrue(f.take())
        self.assertFalse(f.take())

    def test_the_window_remembers_a_marked_refresh_instead_of_dropping_it(self):
        import inspect
        src = inspect.getsource(app)
        self.assertIn("self.rails_kick.timeout.connect(self.refresh_marked)", src)
        self.assertIn("self.bridge.again.connect(self.refresh_marked)", src)
        marked = src.split("def refresh_marked(self):")[1].split("def refresh(self):")[0]
        self.assertIn("self.follow_up.remember()", marked)


class ReceiverTimeTests(unittest.TestCase):
    def test_the_receivers_time_is_read_as_utc(self):
        self.assertEqual(p.tpv_time({"time": "1970-01-01T00:01:40.000Z"}), 100.0)
        self.assertEqual(p.tpv_time({"time": "1970-01-01T00:01:40"}), 100.0)
        self.assertEqual(p.tpv_time({"time": "1970-01-01T00:01:40.500Z"}), 100.5)

    def test_no_time_or_rubbish_is_none(self):
        for tpv in (None, {}, {"time": ""}, {"time": None}, {"time": 12}, {"time": "yesterday"}):
            self.assertIsNone(p.tpv_time(tpv))

    def test_a_first_report_that_agrees_with_the_clock_is_new(self):
        self.assertEqual(p.reading_step(fix(T0 - 1), None, T0), "new")

    def test_a_first_report_that_is_stale_or_under_a_wrong_clock_waits_for_the_time_to_move(self):
        self.assertEqual(p.reading_step(fix(T0 - 600), None, T0), "start")
        self.assertEqual(p.reading_step(fix(T0), None, 1000.0), "start")           # clock never set: 1970

    def test_the_same_report_again_is_no_fresher(self):
        self.assertEqual(p.reading_step(fix(T0), T0, T0 + 30), "same")

    def test_a_later_time_is_a_new_reading_whatever_this_computers_clock_says(self):
        self.assertEqual(p.reading_step(fix(T0 + 4), T0, 1000.0), "new")

    def test_a_slightly_earlier_report_is_ignored_and_a_receiver_that_reset_its_clock_starts_again(self):
        self.assertEqual(p.reading_step(fix(T0 - 2), T0, T0), "old")
        self.assertEqual(p.reading_step(fix(T0 - 86400), T0, T0), "start")

    def test_a_receiver_with_no_time_yet_is_tolerated(self):
        self.assertEqual(p.reading_step(fix(), None, T0), "new")
        self.assertEqual(p.reading_step({"class": "TPV", "mode": 1}, T0, T0), "new")


class PositionFreshnessTests(unittest.TestCase):
    def make(self, answers, wall=T0):
        self.clock, self.wall = Clock(), Clock(wall)
        pos = p.Position(query=lambda: [json.dumps(answers.pop(0))] if answers else [], clock=self.clock,
                         wall=self.wall)
        return pos

    def ask(self, pos, seconds=p.POLL_SECONDS):
        self.clock.t += seconds
        self.wall.t += seconds
        pos.poll(background=False)

    def test_gpsd_repeating_an_old_report_does_not_keep_it_fresh(self):
        pos = self.make([fix(T0)] * 10)
        pos.poll(background=False)
        self.assertEqual(pos.current()["lat"], 51.5)
        for _ in range(4):                                   # 16 seconds of the very same report
            self.ask(pos)
        self.assertIsNone(pos.current())

    def test_a_receiver_that_keeps_reporting_keeps_the_position_fresh(self):
        pos = self.make([fix(T0 + 4 * i, lat=50.0 + i) for i in range(6)])
        pos.poll(background=False)
        for _ in range(5):
            self.ask(pos)
        self.assertEqual(pos.current()["lat"], 55.0)

    def test_a_stale_report_at_the_start_is_never_handed_out(self):
        pos = self.make([fix(T0 - 3600)] * 5)
        pos.poll(background=False)
        self.assertIsNone(pos.current())
        self.ask(pos)
        self.assertIsNone(pos.current())

    def test_a_wrong_system_clock_only_delays_the_first_position_by_one_reading(self):
        pos = self.make([fix(T0), fix(T0 + 4, lat=52.0)], wall=5000.0)
        pos.poll(background=False)
        self.assertIsNone(pos.current())
        self.ask(pos)
        self.assertEqual(pos.current()["lat"], 52.0)

    def test_the_system_clock_being_set_does_not_change_how_old_a_reading_is(self):
        pos = self.make([fix(T0)])
        pos.poll(background=False)
        self.wall.t -= 86400                                 # the clock jumps; the forward-only one does not
        self.assertIsNotNone(pos.current())
        self.wall.t += 2 * 86400
        self.assertIsNotNone(pos.current())
        self.clock.t += p.MAX_AGE + 1
        self.assertIsNone(pos.current())

    def test_expiry_uses_a_clock_that_only_runs_forwards(self):
        self.assertIs(p.Position().clock, time.monotonic)
        self.assertIs(p.GpsdLines().clock, time.monotonic)

    def test_the_reading_is_forgotten_when_the_gps_rail_goes_off(self):
        class Win:
            snap = {"rails": {"GPS": "on"}}
            position = None
        win = Win()
        win.position = self.make([fix(T0), fix(T0)])
        win.position.poll(background=False)
        self.assertIsNotNone(p.current(win))
        win.snap = {"rails": {"GPS": "off"}}
        self.assertIsNone(p.current(win))
        self.assertIsNone(win.position.tpv)
        win.snap = {"rails": {"GPS": "on"}}                  # back on at once: the old reading must not come back
        self.assertIsNone(win.position.current())
        self.assertIsNone(win.position.receiver_time)


class Proc:
    """A process whose output can be held back, to stand for the moment between it ending and its last lines
    being read."""

    def __init__(self, out="", err="", code=0, hold=False):
        self.code, self.gate = code, threading.Event()
        if not hold:
            self.gate.set()
        gate = self.gate

        class Held(io.StringIO):
            def __iter__(self):
                gate.wait(5)
                return super().__iter__()

            def read(self, *a):
                gate.wait(5)
                return super().read(*a)
        self.stdout, self.stderr = Held(out), Held(err)

    def poll(self):
        return self.code

    def terminate(self):
        pass

    def wait(self, t=None):
        return self.code


REC = '{"t": 1.0, "frames": 1, "rssi": -60, "links": {}}\n'


def settle(feed):
    for reader in feed.readers:
        reader.join(5)


class FeedEndTests(unittest.TestCase):
    def test_a_process_that_has_gone_is_not_finished_until_its_output_is_in(self):
        proc = Proc(REC, "Error: hci1 is busy\n", code=1, hold=True)
        feed = s.AirFeed(6, runner=lambda *a, **k: proc)
        self.assertFalse(feed.alive())                       # the old question says it is over already...
        self.assertEqual((feed.records(), feed.error), ([], ""))   # ...with nothing heard and no error
        self.assertFalse(feed.finished())                    # the new one does not
        proc.gate.set()
        settle(feed)
        self.assertTrue(feed.finished())
        self.assertEqual(len(feed.records()), 1)
        self.assertEqual(feed.problem(), "Error: hci1 is busy")
        self.assertEqual(feed.exit_code(), 1)

    def test_a_clean_end_has_no_problem(self):
        feed = s.AirFeed(6, runner=lambda *a, **k: Proc(REC))
        settle(feed)
        self.assertTrue(feed.finished())
        self.assertEqual((feed.problem(), feed.exit_code()), ("", 0))

    def test_a_failure_code_with_nothing_said_is_still_a_problem(self):
        feed = s.AirFeed(6, runner=lambda *a, **k: Proc("", "", code=3))
        settle(feed)
        self.assertIn("code 3", feed.problem())

    def test_a_running_process_is_not_finished_and_has_no_exit_code(self):
        proc = Proc(REC)
        proc.code = None
        feed = s.AirFeed(6, runner=lambda *a, **k: proc)
        settle(feed)
        self.assertFalse(feed.finished())
        self.assertIsNone(feed.exit_code())

    def test_pipes_that_never_close_do_not_leave_the_page_waiting_for_ever(self):
        proc = Proc(REC, hold=True)
        feed = s.AirFeed(6, runner=lambda *a, **k: proc)
        clock = Clock()
        self.assertFalse(feed.finished(clock))
        clock.t += feed.READERS_GRACE + 1
        self.assertTrue(feed.finished(clock))
        proc.gate.set()

    def test_the_bluetooth_feed_has_the_same_ending(self):
        feed = bt.Feed(args=["x"], runner=lambda *a, **k: Proc("", "", code=2))
        settle(feed)
        self.assertTrue(feed.finished())
        self.assertFalse(bt.scan_again(feed.problem(), True, feed.exit_code()))

    def test_a_scan_that_failed_silently_is_not_started_again(self):
        self.assertTrue(bt.scan_again("", True, 0))
        self.assertFalse(bt.scan_again("", True, 1))
        self.assertFalse(bt.scan_again("", True, -15))
        self.assertFalse(bt.scan_again("", True, None))      # ended, but how is not known: not clean
        self.assertFalse(bt.scan_again("", False, 0))

    def test_every_page_asks_finished_before_it_takes_the_records(self):
        import inspect
        from ops_gui import survey
        for mod in (bt, survey, s):
            src = inspect.getsource(mod)
            self.assertIn(".finished()", src, mod.__name__)
            self.assertNotIn("not self.feed.alive()", src, mod.__name__)
            body = src.split(".finished()")[1]
            self.assertIn("self.feed.records()", body[:200], mod.__name__)


class FieldLogHealthTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.now = T0

    def log(self, directory=None, cap=fl.FILE_CAP):
        return fl.FieldLog(lambda: True, directory or self.dir.name, clock=lambda: self.now, cap=cap)

    def test_a_written_line_records_when(self):
        log = self.log()
        self.assertIsNone(log.ok_at)
        self.assertTrue(log.event("state", n=1))
        self.assertEqual((log.ok_at, log.failure, log.news()), (T0, "", ""))

    def test_a_folder_that_cannot_be_written_is_a_failure_with_a_reason(self):
        log = self.log("/proc/nope/never")
        self.assertFalse(log.event("state", n=1))
        self.assertIn("could not be written", log.failure)
        self.assertEqual(log.failed_at, T0)
        self.assertIsNone(log.ok_at)

    def test_the_failure_is_news_once_not_once_a_line(self):
        log = self.log("/proc/nope/never")
        log.event("state", n=1)
        self.assertTrue(log.news())
        for _ in range(5):
            log.event("gps", n=2)
            self.assertEqual(log.news(), "")

    def test_a_full_disk_says_so(self):
        log = self.log()
        real = os.open

        def no_space(path, *a, **k):
            if str(path).endswith(".jsonl"):
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
            return real(path, *a, **k)
        orig, fl.os.open = fl.os.open, no_space
        try:
            self.assertFalse(log.event("state", n=1))
        finally:
            fl.os.open = orig
        self.assertIn("no space left", log.failure.lower())

    def test_reaching_the_daily_cap_is_a_failure_too(self):
        log = self.log(cap=200)
        while log.event("state", text="x" * 40) and not log.failure:
            pass
        self.assertIn("full", log.failure)
        self.assertIsNotNone(log.ok_at)                      # the last real line is still on record
        self.assertFalse(log.event("state", text="more"))
        self.assertIn("200 bytes", log.news())
        self.assertEqual(log.news(), "")

    def test_writing_again_clears_it_and_a_later_failure_is_news_again(self):
        log = self.log(cap=200)
        while not log.failure:
            log.event("state", text="x" * 40)
        log.news()
        self.now += 86400                                    # a new day, a new file
        self.assertTrue(log.event("state", n=1))
        self.assertEqual((log.failure, log.ok_at), ("", self.now))
        log.cap = 0
        log.event("state", n=2)
        log.event("state", n=3)
        self.assertTrue(log.news())

    def test_logging_switched_off_is_not_a_failure(self):
        log = fl.FieldLog(lambda: False, "/proc/nope/never")
        self.assertFalse(log.event("state", n=1))
        self.assertEqual(log.failure, "")

    def test_what_settings_shows(self):
        log = self.log()
        clock = lambda t: "12:00:00"
        self.assertEqual(fl.status_text(log, False, clock), "")
        self.assertEqual(fl.status_text(None, True, clock), "")
        self.assertIn("Nothing has been written yet", fl.status_text(log, True, clock))
        log.event("state", n=1)
        self.assertEqual(fl.status_text(log, True, clock), "Field log is being written. The last line was written at 12:00:00.")
        log.cap = 0
        log.event("state", n=2)
        log.event("state", n=3)
        text = fl.status_text(log, True, clock)
        self.assertIn("NOT being written", text)
        self.assertIn("full", text)
        self.assertIn("12:00:00", text)
        bad = self.log("/proc/nope/never")
        bad.event("state", n=1)
        self.assertIn("Nothing has been written this session", fl.status_text(bad, True, clock))

    def test_settings_and_the_window_use_it(self):
        import inspect
        from ops_gui import settings_ui
        self.assertIn('texts["fieldlog"].setText', inspect.getsource(settings_ui))
        self.assertIn("log.news()", inspect.getsource(fl.attach))


if __name__ == "__main__":
    unittest.main()
