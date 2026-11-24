"""
Tests for installing apps on the Flipper and reading or driving its header pins.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402

ELF = b"\x7fELF" + b"\x00" * 60


def fap(directory, name="air.fap", body=ELF):
    path = os.path.join(directory, name)
    with open(path, "wb") as f:
        f.write(body)
    return path


class AppCheckTests(unittest.TestCase):
    def test_good_app(self):
        with tempfile.TemporaryDirectory() as d:
            remote, data = a.flipper_check_app(fap(d), "GPIO")
            self.assertEqual(remote, "/ext/apps/GPIO/air.fap")
            self.assertEqual(data, ELF)

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as d:
            for local, cat in ((fap(d, "x.txt"), "Tools"), (fap(d, "y.fap", b"MZ" + b"0" * 60), "Tools"),
                               (fap(d, "z.fap"), "Evil"), (os.path.join(d, "none.fap"), "Tools"),
                               (fap(d, "../up.fap".replace("../", "")), "../x")):
                with self.assertRaises(a.FlipperError):
                    a.flipper_check_app(local, cat)

    def test_size_limit_and_odd_names(self):
        with tempfile.TemporaryDirectory() as d:
            big = fap(d, "big.fap", ELF + b"0" * a.FLIPPER_MAX_APP_BYTES)
            with self.assertRaises(a.FlipperError):
                a.flipper_check_app(big, "Tools")
            with self.assertRaises(a.FlipperError):
                a.flipper_check_app(fap(d, "bad;name.fap"), "Tools")

    def test_install_writes_checks_and_refuses_to_overwrite(self):
        import hashlib
        rpc = mock.MagicMock()
        rpc.__enter__.return_value = rpc
        rpc.exists.side_effect = lambda p: p == "/ext/apps/Tools"            # folder there, file not
        rpc.md5.return_value = hashlib.md5(ELF).hexdigest()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(a, "FlipperRpc", return_value=rpc), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(a.flipper_install(None, fap(d), "Tools"), "/ext/apps/Tools/air.fap")
            rpc.write.assert_called_once()
            rpc.mkdir.assert_not_called()
            rpc.exists.side_effect = lambda p: True                           # now the file exists
            with self.assertRaises(a.FlipperError):
                a.flipper_install(None, fap(d), "Tools")
            a.flipper_install(None, fap(d), "Tools", force=True)

    def test_new_category_folder_is_made_and_bad_checksum_fails(self):
        rpc = mock.MagicMock()
        rpc.__enter__.return_value = rpc
        rpc.exists.return_value = False
        rpc.md5.return_value = "deadbeef"
        with tempfile.TemporaryDirectory() as d, mock.patch.object(a, "FlipperRpc", return_value=rpc), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(a.FlipperError):
                a.flipper_install(None, fap(d), "GPIO")
            rpc.mkdir.assert_called_once_with("/ext/apps/GPIO")

    def test_nothing_outside_the_apps_folder(self):
        import inspect
        src = inspect.getsource(a.flipper_install) + inspect.getsource(a.flipper_check_app)
        self.assertNotIn("/int", src)
        self.assertIn("/ext/apps/", src)


class GpioTests(unittest.TestCase):
    def cli(self, answers):
        cli = mock.Mock()
        cli.run.side_effect = lambda cmd: answers.get(cmd, "")
        return cli

    def run_gpio(self, cli, rest, yes=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            a.flipper_gpio(cli, rest, yes)
        return out.getvalue()

    def test_parse(self):
        self.assertEqual(a.parse_flipper_gpio("Pin PC0 <= 1"), 1)
        self.assertEqual(a.parse_flipper_gpio("PC0: 0\n"), 0)
        self.assertEqual(a.parse_flipper_gpio("value 1"), 1)
        self.assertIsNone(a.parse_flipper_gpio("who knows"))
        self.assertIsNone(a.parse_flipper_gpio(""))

    def test_list(self):
        out = self.run_gpio(mock.Mock(), ["list"])
        self.assertIn("PC0  header pin 16", out)
        self.assertEqual(len(out.splitlines()), 8)

    def test_read_makes_the_pin_an_input_first(self):
        cli = self.cli({"gpio read PC0": "Pin PC0 <= 1"})
        self.assertEqual(self.run_gpio(cli, ["read", "PC0"]).strip(), "PC0 reads 1")
        self.assertEqual([c.args[0] for c in cli.run.call_args_list], ["gpio mode PC0 0", "gpio read PC0"])

    def test_set_needs_yes_and_a_real_pin(self):
        cli = self.cli({})
        with self.assertRaises(a.FlipperError):
            self.run_gpio(cli, ["set", "PC0", "1"])
        cli.run.assert_not_called()
        for bad in (["set", "PX9", "1"], ["set", "PC0", "2"], ["read", "PC0; reboot"], ["wipe", "PC0"], []):
            with self.assertRaises(a.FlipperError):
                self.run_gpio(cli, bad, yes=True)
        cli.run.assert_not_called()

    def test_set_with_yes(self):
        cli = self.cli({})
        out = self.run_gpio(cli, ["set", "PB2", "1"], yes=True)
        self.assertEqual([c.args[0] for c in cli.run.call_args_list], ["gpio mode PB2 1", "gpio set PB2 1"])
        self.assertIn("PB2 is now an output set to 1", out)

    def test_an_open_app_gives_a_friendly_error(self):
        cli = self.cli({"gpio mode PC0 0": "this command cannot be run while an application is open"})
        with self.assertRaises(a.FlipperError) as ctx:
            self.run_gpio(cli, ["read", "PC0"])
        self.assertIn("home screen", str(ctx.exception))


class CommandTests(unittest.TestCase):
    def test_usage_and_arity(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(a.flipper_command(["install"]), 1)
            self.assertEqual(a.flipper_command(["gpio"]), 1)
            self.assertEqual(a.flipper_command(["install", "x.fap", "--category"]), 1)
        self.assertIn("install APP.fap", out.getvalue())

    def test_gpio_cannot_send_arbitrary_commands(self):
        import inspect
        src = inspect.getsource(a.flipper_gpio)
        for word in ("os.system", "subprocess", "shell"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
