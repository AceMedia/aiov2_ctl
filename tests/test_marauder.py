"""
Tests for the passive Marauder wrapper, using a fake board on a pty.

    python3 -m unittest discover -s tests
"""
import io
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402


class FakeMarauder:
    def __init__(self):
        self.master, self.slave = os.openpty()
        self.path = os.ttyname(self.slave)
        self.received = []
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def say(self, text):
        os.write(self.master, (text + "\r\n").encode())

    def _serve(self):
        buf = b""
        while not self._stop:
            try:
                buf += os.read(self.master, 1024)
            except OSError:
                return
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                cmd = line.decode().strip("\r")
                self.received.append(cmd)
                if cmd == "scanall":
                    self.say("Starting Wifi scan")
                    self.say("-50 Ch: 6 BSSID: aa:bb:cc:dd:ee:ff ESSID: HomeNet")
                elif cmd == "list -a":
                    self.say("[0][CH:6] HomeNet -50")
                elif cmd == "list -c":
                    self.say("[0] 11:22:33:44:55:66 -> aa:bb:cc:dd:ee:ff")
                elif cmd == "info":
                    self.say("Marauder v1.20")
                elif cmd == "stopscan":
                    self.say("Stopped")

    def close(self):
        self._stop = True
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


@unittest.skipUnless(hasattr(os, "openpty"), "needs a Unix pty")
class MarauderCommandTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMarauder()
        self.addCleanup(self.fake.close)
        self.logdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.logdir.cleanup)
        patcher = mock.patch.object(a, "MARAUDER_LOG_DIR", self.logdir.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cmd(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = a.marauder_command([*args, "--port", self.fake.path])
        return rc, out.getvalue()

    def test_info(self):
        rc, out = self.run_cmd("info")
        self.assertEqual(rc, 0)
        self.assertIn("Marauder v1.20", out)

    def test_scan_streams_logs_and_stops(self):
        rc, out = self.run_cmd("scan", "all", "--seconds", "1")
        self.assertEqual(rc, 0)
        self.assertIn("HomeNet", out)
        self.assertEqual(self.fake.received[0], "scanall")
        self.assertIn("stopscan", self.fake.received)
        self.assertLess(self.fake.received.index("scanall"),
                        self.fake.received.index("stopscan"))
        self.assertIn("list -a", self.fake.received)

        logs = os.listdir(self.logdir.name)
        self.assertEqual(len(logs), 1)
        path = os.path.join(self.logdir.name, logs[0])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as f:
            self.assertIn("HomeNet", f.read())

    def test_scan_always_stops_on_interrupt(self):
        with a.MarauderSerial(self.fake.path) as ser:
            log = a.MarauderLog(self.logdir.name)
            with mock.patch.object(ser, "read_lines", side_effect=KeyboardInterrupt):
                a.marauder_scan(ser, "all", 5, log, say=lambda s: None)
            log.close()
        time.sleep(0.2)
        self.assertIn("stopscan", self.fake.received)

    def test_channel_set_and_validation(self):
        rc, _ = self.run_cmd("channel", "11")
        self.assertEqual(rc, 0)
        self.assertIn("channel -s 11", self.fake.received)
        for bad in ("0", "15", "x", "1;reboot", "-s"):
            rc, _ = self.run_cmd("channel", bad)
            self.assertEqual(rc, 1, bad)

    def test_active_and_unknown_commands_never_reach_the_board(self):
        before = len(self.fake.received)
        for cmd in (("attack", "-t", "deauth"), ("blespam", "-t", "all"),
                    ("evilportal",), ("raw", "reboot"), ("scan", "pmkid"),
                    ("list", "aps;reboot"), ("info", "extra")):
            rc, out = self.run_cmd(*cmd)
            self.assertEqual(rc, 1, cmd)
        time.sleep(0.2)
        self.assertEqual(len(self.fake.received), before)

    def test_list_defaults_to_aps(self):
        rc, out = self.run_cmd("list")
        self.assertEqual(rc, 0)
        self.assertIn("HomeNet", out)


    def test_every_scan_mode_is_passive_and_starts_its_own_command(self):
        for mode, command in a.MARAUDER_SCAN_MODES.items():
            self.assertNotIn(command, a.MARAUDER_REFUSED)
            self.assertNotIn(mode, a.MARAUDER_REFUSED)
            self.assertTrue(command.startswith(("scan", "sniff", "packetcount", "csimotion")), command)
            self.assertNotIn(" ", command)
        rc, _ = self.run_cmd("scan", "probe", "--seconds", "1")
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake.received[0], "sniffprobe")
        self.assertIn("stopscan", self.fake.received)

    def test_modes_listing(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(a.marauder_command(["modes", "--json"]), 0)
        data = json.loads(out.getvalue())
        self.assertEqual({d["mode"] for d in data}, set(a.MARAUDER_MODE_INFO))
        self.assertTrue(all(d["listens_for"] for d in data))

    def test_refused_commands_say_why_and_never_reach_the_board(self):
        before = len(self.fake.received)
        for cmd in (("attack",), ("sniffpmkid",), ("scan", "evilportal"), ("blespam",), ("beaconspam",), ("deauth",)):
            rc, out = self.run_cmd(*cmd)
            self.assertEqual(rc, 1, cmd)
            self.assertIn("only listens", out)
        time.sleep(0.2)
        self.assertEqual(len(self.fake.received), before)

    def test_list_ssids(self):
        rc, _ = self.run_cmd("list", "ssids")
        self.assertEqual(rc, 0)
        self.assertIn("list -s", self.fake.received)

    def test_bad_seconds(self):
        rc, out = self.run_cmd("scan", "--seconds", "0")
        self.assertEqual(rc, 1)


class PortChoiceTests(unittest.TestCase):
    def dev(self, kind, tty):
        return {"kind": kind, "tty": tty, "label": kind, "name": kind,
                "vid": "", "pid": "", "serial": None}

    def test_prefers_esp32_over_flipper(self):
        found = [self.dev("flipper", "/dev/ttyACM0"), self.dev("esp32", "/dev/ttyACM1")]
        with mock.patch.object(a, "detect_companions", return_value=found):
            self.assertEqual(a._marauder_port(), ("/dev/ttyACM1", None))

    def test_flipper_fallback_explains_the_bridge(self):
        with mock.patch.object(a, "detect_companions",
                               return_value=[self.dev("flipper", "/dev/ttyACM0")]):
            port, note = a._marauder_port()
        self.assertEqual(port, "/dev/ttyACM0")
        self.assertIn("USB-UART Bridge", note)

    def test_nothing_found(self):
        with mock.patch.object(a, "detect_companions", return_value=[]):
            with self.assertRaises(a.MarauderError):
                a._marauder_port()

    def test_log_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(a.time, "strftime", return_value="marauder-x.log"):
                first = a.MarauderLog(d)
                with self.assertRaises(FileExistsError):
                    a.MarauderLog(d)
                first.close()


if __name__ == "__main__":
    unittest.main()
