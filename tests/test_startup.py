"""Opening in full screen at login, and getting the taskbar back (ops_gui/startup.py). No screen is needed."""
import os
import shlex
import tempfile
import unittest

from ops_gui import apps, startup


class AutostartTests(unittest.TestCase):
    def test_the_file_goes_in_the_persons_own_autostart_folder(self):
        self.assertEqual(startup.path("/home/u", {}), "/home/u/.config/autostart/uconsole-ops-fullscreen.desktop")
        self.assertEqual(startup.path("/home/u", {"XDG_CONFIG_HOME": "/cfg"}), "/cfg/autostart/uconsole-ops-fullscreen.desktop")

    def test_the_entry_starts_the_app_in_full_screen_from_its_folder_and_nothing_else(self):
        entry = apps.parse_entry(startup.entry("/home/u/aiov2_ctl-ace"))
        self.assertEqual(entry["Type"], "Application")
        argv = apps.command(entry["Exec"])
        self.assertEqual(argv[:2], ["sh", "-c"])
        self.assertEqual(shlex.split(argv[2]), ["cd", "/home/u/aiov2_ctl-ace", "2>/dev/null", "&&", "exec",
                                                "/usr/bin/python3", "-m", "ops_gui", "--fullscreen"])
        for rail in ("GPS", "LORA", "SDR", "USB", "aiov2_ctl "):
            self.assertNotIn(rail, entry["Exec"])

    def test_a_folder_with_a_space_or_a_quote_in_its_name_still_works(self):
        entry = apps.parse_entry(startup.entry("/home/u/my ops' copy"))
        self.assertEqual(shlex.split(apps.command(entry["Exec"])[2])[1], "/home/u/my ops' copy")

    def test_switching_it_on_writes_the_file_and_off_removes_it(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertFalse(startup.enabled(home, {}))
            path = startup.set_enabled(True, "/repo", home, {})
            self.assertTrue(startup.enabled(home, {}))
            self.assertIn("--fullscreen", open(path).read())
            startup.set_enabled(True, "/repo", home, {})            # on twice is still on
            self.assertTrue(startup.enabled(home, {}))
            startup.set_enabled(False, "/repo", home, {})
            self.assertFalse(os.path.exists(path))
            startup.set_enabled(False, "/repo", home, {})           # off twice is not an error

    def test_the_flag_is_what_main_looks_for(self):
        self.assertTrue(startup.wants_fullscreen(["ops_gui", "--fullscreen"]))
        self.assertFalse(startup.wants_fullscreen(["ops_gui", "--tab", "Wi-Fi"]))


class PeekTests(unittest.TestCase):
    """The window is 720 tall. In full screen the bottom edge is y 718 and 719."""
    FULL = {"peeking": False, "count": 0}

    def test_a_pointer_passing_the_bottom_edge_does_nothing(self):
        state, action = startup.peek_step(self.FULL, 719, 720)
        self.assertIsNone(action)
        state, action = startup.peek_step(state, 400, 720)
        self.assertEqual((state, action), (self.FULL, None))

    def test_resting_on_the_bottom_edge_steps_out_for_the_taskbar(self):
        state, action = startup.peek_step(self.FULL, 719, 720)
        state, action = startup.peek_step(state, 718, 720)
        self.assertEqual(action, "peek")
        self.assertTrue(state["peeking"])

    def test_near_the_bottom_is_not_the_edge(self):
        state = self.FULL
        for _ in range(5):
            state, action = startup.peek_step(state, 700, 720)
            self.assertIsNone(action)

    def test_while_the_taskbar_is_showing_it_stays_until_the_pointer_is_back_in_the_app(self):
        state = {"peeking": True, "count": 0}
        for y, inside in ((660, False), (640, True), (620, True)):   # on the taskbar, then only just above it
            state, action = startup.peek_step(state, y, 680, inside)
            self.assertIsNone(action, (y, inside))
        state, action = startup.peek_step(state, 300, 680, True)
        self.assertIsNone(action, "once is not enough")
        state, action = startup.peek_step(state, 280, 680, True)
        self.assertEqual(action, "return")
        self.assertEqual(state, self.FULL)

    def test_a_pointer_that_is_not_over_the_window_never_counts(self):
        state = self.FULL
        for _ in range(4):
            state, action = startup.peek_step(state, 719, 720, inside=False)
            self.assertIsNone(action)
        state = {"peeking": True, "count": 0}
        for _ in range(4):
            state, action = startup.peek_step(state, -40, 680, inside=False)
            self.assertIsNone(action)


if __name__ == "__main__":
    unittest.main()
