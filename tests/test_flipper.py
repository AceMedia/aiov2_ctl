"""
Tests for the Flipper CLI client, using a fake Flipper on a pseudo terminal.

    python3 -m unittest discover -s tests

These need a Unix pty, so they are skipped elsewhere.
"""
import io
import os
import sys
import threading
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402

RESPONSES = {
    "device_info": "hardware_name : Aceflip\r\nhardware_ver : 12\r\nfirmware_version : 1.4.3\r\n",
    "power info": "charge.level : 83\r\ncharge.state : discharging\r\nsomething.else : x\r\n",
    "storage list /ext": "[D] apps\r\n[F] notes.txt 123b\r\n",
    "storage list /nope": "Storage error: invalid name\r\n",
    "loader list": "Applications:\r\n\tClock\r\n",
}


class FakeFlipper:
    """Echoes input like the real CLI and answers from RESPONSES."""

    def __init__(self, silent=False):
        self.master, self.slave = os.openpty()
        self.path = os.ttyname(self.slave)
        self.silent = silent
        self.received = []
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        buf = b""
        while not self._stop:
            try:
                chunk = os.read(self.master, 1024)
            except OSError:
                return
            buf += chunk
            while b"\r" in buf:
                line, _, buf = buf.partition(b"\r")
                cmd = line.decode()
                self.received.append(cmd)
                if self.silent:
                    continue
                if cmd == "":
                    os.write(self.master, b"\r\n>: ")
                    continue
                reply = RESPONSES.get(cmd, "Unknown command\r\n")
                os.write(self.master, f"{cmd}\r\n{reply}\r\n>: ".encode())

    def close(self):
        self._stop = True
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


@unittest.skipUnless(hasattr(os, "openpty"), "needs a Unix pty")
class FlipperCliTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeFlipper()
        self.addCleanup(self.fake.close)

    def test_run_strips_echo_and_prompt(self):
        with a.FlipperCli(self.fake.path, timeout=2) as cli:
            out = cli.run("device_info")
        self.assertNotIn("device_info", out)
        self.assertNotIn(">:", out)
        self.assertEqual(a.parse_flipper_kv(out)["firmware_version"], "1.4.3")

    def test_refuses_line_breaks(self):
        with a.FlipperCli(self.fake.path, timeout=2) as cli:
            with self.assertRaises(a.FlipperError):
                cli.run("device_info\rloader open Evil")
        self.assertNotIn("loader open Evil", self.fake.received)

    def test_silent_device_gives_clear_error(self):
        silent = FakeFlipper(silent=True)
        self.addCleanup(silent.close)
        with self.assertRaises(a.FlipperError) as cm:
            a.FlipperCli(silent.path, timeout=0.5).open()
        self.assertIn("No CLI prompt", str(cm.exception))

    def test_missing_port(self):
        with self.assertRaises(a.FlipperError):
            a.FlipperCli("/dev/does-not-exist", timeout=0.5).open()


@unittest.skipUnless(hasattr(os, "openpty"), "needs a Unix pty")
class FlipperCommandTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeFlipper()
        self.addCleanup(self.fake.close)

    def run_cmd(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = a.flipper_command([*args, "--port", self.fake.path])
        return rc, out.getvalue()

    def test_info(self):
        rc, out = self.run_cmd("info")
        self.assertEqual(rc, 0)
        self.assertIn("Aceflip", out)
        self.assertIn("1.4.3", out)
        self.assertIn("charge.level", out)
        self.assertNotIn("something.else", out)

    def test_ls_default_and_parsing(self):
        rc, out = self.run_cmd("ls")
        self.assertEqual(rc, 0)
        self.assertIn("notes.txt", out)
        self.assertIn("storage list /ext", self.fake.received)

    def test_ls_error_is_reported(self):
        rc, out = self.run_cmd("ls", "/nope")
        self.assertEqual(rc, 1)
        self.assertIn("Storage error", out)

    def test_open_sends_loader_command(self):
        rc, _ = self.run_cmd("open", "Clock")
        self.assertEqual(rc, 0)
        self.assertIn("loader open Clock", self.fake.received)

    def test_unsafe_arguments_never_reach_the_device(self):
        for bad in ("Clock; reboot", "../../etc", "a\nb", "", "x" * 300):
            rc, out = self.run_cmd("open", bad)
            self.assertEqual(rc, 1, bad)
        self.assertFalse([c for c in self.fake.received if c.startswith("loader open")])

    def test_usage(self):
        rc, out = self.run_cmd("bogus")
        self.assertEqual(rc, 1)
        self.assertIn("Usage", out)


class ParserTests(unittest.TestCase):
    def test_storage_list(self):
        entries = a.parse_flipper_storage_list("[D] apps\n[F] a b.txt 5kb\n")
        self.assertEqual(entries, [("dir", "apps", ""), ("file", "a b.txt", "5kb")])

    def test_storage_error(self):
        with self.assertRaises(a.FlipperError):
            a.parse_flipper_storage_list("Storage error: not ready")

    def test_safe_arg(self):
        self.assertEqual(a.flipper_safe_arg("/ext/apps_data"), "/ext/apps_data")
        for bad in ("/ext/../int", "a;b", "a|b", "`id`", "$(id)"):
            with self.assertRaises(a.FlipperError):
                a.flipper_safe_arg(bad)

    def test_no_flipper_attached(self):
        with mock.patch.object(a, "detect_companions", return_value=[]):
            with self.assertRaises(a.FlipperError):
                a._flipper_port()


if __name__ == "__main__":
    unittest.main()
