import os
import tempfile
import unittest

import aiov2_ctl as a


class RailsMarkTests(unittest.TestCase):
    """Whoever switches a rail leaves a mark the others watch."""

    def test_the_mark_belongs_to_the_user_even_under_sudo(self):
        path = a.rails_stamp_path({"SUDO_UID": "1000"}, None)
        self.assertIn("1000", path)
        self.assertTrue(path.endswith(".rails"))
        self.assertEqual(a.rails_stamp_path({}, 4242), "/tmp/aiov2_ctl-4242.rails")      # no runtime folder

    def test_leaving_the_mark_writes_the_time_and_can_be_done_again(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.rails")
            self.assertTrue(a.mark_rails_changed(path, now=100.0))
            self.assertEqual(open(path).read().strip(), "100.000")
            self.assertTrue(a.mark_rails_changed(path, now=101.5))
            self.assertEqual(open(path).read().strip(), "101.500")
            self.assertEqual(os.listdir(d), ["x.rails"])                                  # nothing left beside it

    def test_a_mark_that_cannot_be_left_does_not_raise(self):
        self.assertFalse(a.mark_rails_changed("/proc/nope/never.rails"))

    def test_switching_a_rail_leaves_the_mark(self):
        calls, marks = [], []
        real_call, real_mark = a.subprocess.call, a.mark_rails_changed
        a.subprocess.call = lambda args, **kw: calls.append(args) or 0
        a.mark_rails_changed = lambda *args, **kw: marks.append(1) or True
        try:
            a.GpioController.set_gpio(27, True)
        finally:
            a.subprocess.call, a.mark_rails_changed = real_call, real_mark
        self.assertEqual(calls, [["pinctrl", "set", "27", "op", "dh"]])
        self.assertEqual(len(marks), 1)


class OpsShortcutTests(unittest.TestCase):
    """The tray opens the ops app and console through the start menu's own shortcuts."""

    def test_only_shortcuts_that_are_installed_are_offered(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(a.ops_shortcuts(d), [])
            open(os.path.join(d, "uconsole-ops-gui.desktop"), "w").close()
            got = a.ops_shortcuts(d)
            self.assertEqual([(k, label) for k, _, label in got], [("gui", "Open uConsole Ops")])
            open(os.path.join(d, "uconsole-ops-console.desktop"), "w").close()
            self.assertEqual([k for k, _, _ in a.ops_shortcuts(d)], ["gui", "console"])

    def test_the_shortcut_folder_follows_the_desktop_setting(self):
        self.assertEqual(a.ops_shortcut_dir({"XDG_DATA_HOME": "/x"}), "/x/applications")
        self.assertTrue(a.ops_shortcut_dir({}).endswith("/.local/share/applications"))

    def test_the_app_is_started_as_the_start_menu_starts_it(self):
        env = a.ops_launch_env({"QT_QPA_PLATFORM": "xcb", "DISPLAY": ":0"})
        self.assertNotIn("QT_QPA_PLATFORM", env)
        self.assertEqual(env["DISPLAY"], ":0")

    def run_open(self, kind, running):
        ran, started = [], []

        class Done:
            returncode = 0 if running else 1

        def runner(args, **kw):
            ran.append(args)
            return Done()

        def popen(args, **kw):
            started.append((args, kw))
        return a.ops_open(kind, "/p/x.desktop", runner, popen), ran, started

    def test_an_app_that_is_already_open_is_brought_forward_not_started_twice(self):
        result, ran, started = self.run_open("gui", running=True)
        self.assertEqual(result, "already")
        self.assertEqual(started, [])
        self.assertIn(["wmctrl", "-a", a.OPS_GUI_WINDOW], ran)

    def test_an_app_that_is_not_open_is_started_through_its_shortcut(self):
        result, _ran, started = self.run_open("gui", running=False)
        self.assertEqual(result, "opened")
        self.assertEqual(started[0][0], ["gio", "launch", "/p/x.desktop"])
        self.assertTrue(started[0][1]["start_new_session"])

    def test_the_console_always_opens_a_new_window(self):
        result, ran, started = self.run_open("console", running=True)
        self.assertEqual(result, "opened")
        self.assertEqual(ran, [])
        self.assertEqual(len(started), 1)

    def test_a_missing_launcher_program_is_reported_not_raised(self):
        def popen(args, **kw):
            raise OSError("no gio")
        self.assertEqual(a.ops_open("console", "/p/x.desktop", popen=popen), "failed")


if __name__ == "__main__":
    unittest.main()
