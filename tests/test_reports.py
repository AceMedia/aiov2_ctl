"""Tests for the opt in logs, crash reports and manual report."""
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from ops_gui import reports  # noqa: E402


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_redact(self):
        text = "/home/shane/x shane on uconsole-1 aa:bb:cc:dd:ee:ff at 192.168.0.83"
        out = reports.redact(text, home="/home/shane", user="shane", host="uconsole-1")
        for secret in ("shane", "uconsole-1", "aa:bb", "192.168"):
            self.assertNotIn(secret, out)
        self.assertIn("<mac>", out)
        self.assertIn("<ip>", out)

    def test_logging_is_off_unless_enabled(self):
        self.assertFalse(reports.write_log("hello", False))
        self.assertFalse(os.path.exists(reports.log_path()))
        self.assertTrue(reports.write_log("hello", True))
        self.assertIn("hello", reports.tail_log())
        self.assertEqual(os.stat(reports.log_path()).st_mode & 0o777, 0o600)

    def test_crash_hook_respects_the_opt_in_at_crash_time(self):
        state = {"on": False}
        seen = []
        original = sys.excepthook
        sys.excepthook = lambda *a: seen.append(a)
        try:
            reports.install_crash_hook(lambda: state["on"])
            try:
                raise ValueError("boom")
            except ValueError:
                info = sys.exc_info()
            sys.excepthook(*info)
            self.assertEqual(reports.pending_crashes(), [])
            state["on"] = True
            sys.excepthook(*info)
            self.assertEqual(len(reports.pending_crashes()), 1)
            self.assertEqual(len(seen), 2)
        finally:
            sys.excepthook = original

    def test_clear_all(self):
        reports.write_log("x", True)
        reports.save_crash(ValueError, ValueError("e"), None)
        self.assertEqual(reports.clear_all(), 2)
        self.assertEqual(reports.pending_crashes(), [])
        self.assertEqual(reports.tail_log(), "")

    CRASH = ('Traceback (most recent call last):\n'
             '  File "/home/shane/ops-agentic/ops_gui/app.py", line 512, in tick\n'
             '    self.refresh()\n'
             '  File "/home/shane/ops-agentic/ops_gui/wifi_ui.py", line 88, in refresh\n'
             '    row = rows["wlan0"]\n'
             "KeyError: 'wlan0'\n")

    def test_the_same_crash_gets_the_same_code_whatever_the_lines_folder_or_message(self):
        fp, summary = reports.crash_signature(self.CRASH)
        self.assertEqual(summary, "KeyError in wifi_ui.refresh")
        self.assertRegex(fp, "^[0-9a-f]{8}$")
        moved = (self.CRASH.replace("/home/shane/ops-agentic", "/home/someone/elsewhere")
                 .replace("line 512", "line 9").replace("line 88", "line 101").replace("'wlan0'", "'wlan1'"))
        self.assertEqual(reports.crash_signature(moved)[0], fp)

    def test_a_different_crash_gets_a_different_code(self):
        fp = reports.crash_signature(self.CRASH)[0]
        self.assertNotEqual(reports.crash_signature(self.CRASH.replace("KeyError", "TypeError"))[0], fp)
        self.assertNotEqual(reports.crash_signature(self.CRASH.replace("in refresh", "in redraw"))[0], fp)

    def test_text_that_is_no_crash_has_no_code(self):
        self.assertIsNone(reports.crash_signature(""))
        self.assertIsNone(reports.crash_signature("Traceback (most recent call last):\n"))
        self.assertEqual(reports.crash_title(""), "uConsole app crash")
        self.assertIsNone(reports.find_url(""))

    def test_the_title_and_the_search_carry_the_code_and_nothing_of_the_report(self):
        fp = reports.crash_signature(self.CRASH)[0]
        self.assertEqual(reports.crash_title(self.CRASH), f"Crash {fp}: KeyError in wifi_ui.refresh")
        url = reports.find_url(self.CRASH)
        self.assertTrue(url.startswith("https://github.com/AceMedia/aiov2_ctl/issues?q="))
        self.assertIn(fp, url)
        for private in ("shane", "wlan0", "ops-agentic"):
            self.assertNotIn(private, url)
        new = reports.issue_url(reports.crash_title(self.CRASH), ("crash",))
        self.assertIn("labels=crash", new)
        self.assertNotIn("shane", new)

    def test_the_crash_notice_is_for_the_newest_crash_until_it_is_dismissed(self):
        self.assertIsNone(reports.crash_notice(None))
        n = reports.crash_notice(("crash-1.txt", self.CRASH))
        self.assertEqual(n["id"], "crash-1.txt")
        self.assertIn("KeyError in wifi_ui.refresh", n["body"])
        self.assertIn("Nothing has been sent", n["body"])
        self.assertIsNone(reports.crash_notice(("crash-1.txt", self.CRASH), "crash-1.txt"))
        self.assertIsNotNone(reports.crash_notice(("crash-2.txt", self.CRASH), "crash-1.txt"))

    def test_the_newest_crash_is_read_back(self):
        self.assertIsNone(reports.latest_crash())
        try:
            raise ValueError("e")
        except ValueError as e:
            reports.save_crash(ValueError, e, e.__traceback__)
        name, text = reports.latest_crash()
        self.assertTrue(name.startswith("crash-"))
        self.assertIn("ValueError in test_reports.test_the_newest_crash_is_read_back",
                      reports.crash_signature(text)[1])

    def test_issue_url_never_carries_the_report(self):
        url = reports.issue_url("Hi there")
        self.assertTrue(url.startswith("https://github.com/AceMedia/aiov2_ctl/issues/new?"))
        self.assertIn("title=Hi+there", url)
        self.assertLess(len(url), 1000)


if __name__ == "__main__":
    unittest.main()
