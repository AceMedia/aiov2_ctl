"""The helpers that swap between the app and the terminal console."""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from ops_agent import handoff  # noqa: E402


class HandoffTests(unittest.TestCase):
    def test_display_detection(self):
        self.assertTrue(handoff.has_display({"WAYLAND_DISPLAY": "wayland-0"}))
        self.assertTrue(handoff.has_display({"DISPLAY": ":0"}))
        self.assertFalse(handoff.has_display({}))

    def test_terminal_choice_follows_what_is_installed(self):
        argv = handoff.terminal_argv("/home/u/ops", which=lambda p: p if p == "xterm" else None, env={})
        self.assertEqual(argv[:3], ["xterm", "-e", "sh"])
        self.assertIn("ops.py --no-prompts", argv[-1])
        first = handoff.terminal_argv("/home/u/ops", which=lambda p: p, env={})
        self.assertEqual(first[0], "x-terminal-emulator")           # the system's own choice comes first
        lx = handoff.terminal_argv("/home/u/ops", which=lambda p: None if p == "x-terminal-emulator" else p, env={})
        self.assertEqual(lx[0], "lxterminal")
        self.assertIsNone(handoff.terminal_argv("/x", which=lambda p: None, env={}))

    def test_paths_with_spaces_are_quoted(self):
        cmd = handoff.console_command("/home/a b/ops")
        self.assertIn("'/home/a b/ops'", cmd)
        self.assertIn("-m ops_gui --tab SDR", handoff.app_command("/x", "SDR"))
        self.assertNotIn("--tab", handoff.app_command("/x"))

    def test_delayed_wraps_for_sh(self):
        self.assertEqual(handoff.delayed("echo hi", 2), ["sh", "-c", "sleep 2; echo hi"])
        self.assertEqual(handoff.delayed(["a b", "c"], 1)[2], "sleep 1; 'a b' c")


if __name__ == "__main__":
    unittest.main()


class ConsoleFlagTests(unittest.TestCase):
    def test_no_prompts_keeps_rails_as_they_are(self):
        from ops_agent import power
        self.assertEqual(power.startup_enabled({"GPS": False, "SDR": True}), {"GPS": False, "SDR": True})
        self.assertEqual(power.startup_enabled(None), {})

    def test_the_flag_exists_and_skips_the_exit_prompt(self):
        import inspect
        import importlib
        src = inspect.getsource(importlib.import_module("ops_agent.main").main)
        self.assertIn('"--no-prompts"', src)
        self.assertIn("if not args.no_prompts:", src)


class ShiftAliasTests(unittest.TestCase):
    def test_capital_letter_is_added(self):
        out = handoff.with_shift_aliases([("shift+g", "open_app", "Open app"), ("c", "next", "x")])
        self.assertEqual(out[0], ("shift+g,G", "open_app", "Open app"))
        self.assertEqual(out[1], ("c", "next", "x"))

    def test_other_keys_are_left_alone(self):
        keys = [("shift+tab", "a", "b"), ("ctrl+g", "a", "b"), ("shift+1", "a", "b"), ("G", "a", "b")]
        self.assertEqual(handoff.with_shift_aliases(keys), keys)

    def test_not_added_twice(self):
        out = handoff.with_shift_aliases([("shift+g,G", "a", "b")])
        self.assertEqual(out[0][0], "shift+g,G")


class TerminalPreferenceTests(unittest.TestCase):
    ALL = {"lxterminal", "x-terminal-emulator", "foot", "alacritty", "xterm"}

    def which(self, have):
        return lambda name: f"/usr/bin/{name}" if name in have else None

    def test_the_terminal_variable_wins(self):
        argv = handoff.terminal_argv("/opt/ops", self.which(self.ALL | {"kitty"}), {"TERMINAL": "kitty"})
        self.assertEqual(argv[:2], ["kitty", "-e"])
        self.assertIn("ops.py --no-prompts", argv[-1])

    def test_then_the_debian_alternative(self):
        argv = handoff.terminal_argv("/opt/ops", self.which(self.ALL), {})
        self.assertEqual(argv[0], "x-terminal-emulator")

    def test_then_the_known_ones(self):
        argv = handoff.terminal_argv("/opt/ops", self.which({"foot", "xterm"}), {})
        self.assertEqual(argv[0], "foot")
        self.assertIsNone(handoff.terminal_argv("/opt/ops", self.which(set()), {}))

    def test_odd_terminal_values_are_ignored(self):
        for bad in ("/bin/sh", "-x", "a/b", "  "):
            argv = handoff.terminal_argv("/opt/ops", self.which(self.ALL), {"TERMINAL": bad})
            self.assertEqual(argv[0], "x-terminal-emulator", bad)

    def test_a_plain_terminal_has_no_command(self):
        for have, want in (({"lxterminal"}, ["lxterminal", "--working-directory=/home/u"]),
                           ({"x-terminal-emulator"}, ["x-terminal-emulator"]), ({"foot"}, ["foot"]),
                           ({"xterm"}, ["xterm"])):
            argv = handoff.plain_terminal_argv("/home/u", self.which(have), {})
            self.assertEqual(argv, want)
        self.assertIsNone(handoff.plain_terminal_argv("/home/u", self.which(set()), {}))
        self.assertEqual(handoff.plain_terminal_argv("/h", self.which({"kitty"}), {"TERMINAL": "kitty"}), ["kitty"])
