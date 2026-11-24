"""
Tests for the passive Wi-Fi monitor helper used by Sense. No radio is needed.

    python3 -m unittest discover -s tests
"""
import inspect
import io
import os
import struct
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402


def radiotap(signal=-55, flags=0x10, tsft=True, channel=True):
    """A radiotap header with TSFT, flags, rate, channel and signal, as the MT7921 sends them."""
    present = (1 << 0 if tsft else 0) | 1 << 1 | 1 << 2 | (1 << 3 if channel else 0) | 1 << 5
    body = b""
    if tsft:
        body += struct.pack("<Q", 123456)
    body += bytes([flags, 12])
    if channel:
        body += struct.pack("<HH", 2437, 0x0480)
    body += struct.pack("<b", signal)
    length = 8 + len(body)
    return struct.pack("<BBHI", 0, 0, length, present) + body


def dot11(mac=b"\xaa\xbb\xcc\x00\x11\x22", kind=2):
    fc = bytes([kind << 2, 0])
    return fc + b"\x00\x00" + b"\xff" * 6 + mac + b"\xff" * 6 + b"\x00\x00" + b"payload"


class RadiotapTests(unittest.TestCase):
    def test_signal_is_read(self):
        self.assertEqual(a.radiotap_info(radiotap(-61) + dot11())[0], -61)

    def test_without_optional_fields(self):
        raw = radiotap(-70, tsft=False, channel=False) + dot11()
        self.assertEqual(a.radiotap_info(raw)[0], -70)

    def test_bad_checksum_flag(self):
        self.assertTrue(a.radiotap_info(radiotap(flags=0x50) + dot11())[1])
        self.assertFalse(a.radiotap_info(radiotap(flags=0x10) + dot11())[1])

    def test_rubbish_is_refused(self):
        for raw in (b"", b"\x01" * 20, b"\x00\x00\xff\xff\x00\x00\x00\x00" + b"\x00" * 4, radiotap()[:9]):
            self.assertIsNone(a.radiotap_info(raw))


class FrameTests(unittest.TestCase):
    def test_transmitter_and_signal(self):
        self.assertEqual(a.monitor_frame(radiotap(-58) + dot11()), ("aa:bb:cc:00:11:22", -58))

    def test_bad_frames_are_dropped(self):
        self.assertIsNone(a.monitor_frame(radiotap(flags=0x50) + dot11()))
        self.assertIsNone(a.monitor_frame(radiotap() + dot11(kind=1)))                 # control frame
        self.assertIsNone(a.monitor_frame(radiotap() + dot11(mac=b"\x01\xbb\xcc\x00\x11\x22")))  # group address
        self.assertIsNone(a.monitor_frame(radiotap() + b"\x80\x00"))                   # truncated
        self.assertIsNone(a.monitor_frame(radiotap(signal=0) + dot11()))               # impossible signal

    def test_summary(self):
        rec = a.monitor_summary([("a", -50), ("a", -60), ("b", -70)], 10.0)
        self.assertEqual(rec["frames"], 3)
        self.assertEqual(rec["links"]["a"], [2, -55.0])
        self.assertEqual(rec["rssi"], -60.0)
        empty = a.monitor_summary([], 11.0)
        self.assertEqual((empty["frames"], empty["rssi"], empty["links"]), (0, None, {}))

    def test_summary_keeps_only_the_busiest(self):
        seen = [(f"m{i}", -50) for i in range(20) for _ in range(i + 1)]
        self.assertEqual(len(a.monitor_summary(seen, 1.0)["links"]), a.MONITOR_LINKS_PER_SECOND)


class CommandTests(unittest.TestCase):
    def run_cmd(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = a.monitor_command(args)
        return code, out.getvalue() + err.getvalue()

    def test_bad_arguments(self):
        for args in (["--channel", "99"], ["--channel", "x"], ["--seconds", "0"], ["--seconds", "99999"],
                     ["--iface", "../etc"], ["--bogus"], ["--channel"]):
            self.assertEqual(self.run_cmd(args)[0], 1, args)

    def test_needs_root(self):
        with mock.patch.object(a.os, "geteuid", return_value=1000):
            code, text = self.run_cmd(["--channel", "6"])
        self.assertEqual(code, 1)
        self.assertIn("root", text)

    def test_refuses_a_connected_radio(self):
        done = mock.Mock(stdout="Connected to aa:bb:cc:dd:ee:ff (on wlan1)")
        with mock.patch.object(a.os, "geteuid", return_value=0), \
                mock.patch.object(a, "_tool", return_value="/x"), \
                mock.patch.object(a.os.path, "isdir", return_value=True), \
                mock.patch.object(a, "_monitor_cmd", return_value=done) as run:
            code, text = self.run_cmd([])
        self.assertEqual(code, 1)
        self.assertIn("connected", text)
        self.assertFalse([c for c in run.call_args_list if "monitor" in c.args])

    def test_always_restores_even_when_setup_fails(self):
        import subprocess
        calls = []

        def fake(*cmd, check=True):
            calls.append(cmd)
            if "monitor" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="not supported")
            return mock.Mock(stdout="Not connected.")
        with mock.patch.object(a.os, "geteuid", return_value=0), \
                mock.patch.object(a, "_tool", side_effect=lambda n: "/bin/" + n), \
                mock.patch.object(a.os.path, "isdir", return_value=True), \
                mock.patch.object(a, "_monitor_cmd", side_effect=fake):
            code, text = self.run_cmd(["--channel", "6"])
        self.assertEqual(code, 1)
        self.assertIn("not supported", text)
        self.assertTrue(any("managed" in c for c in calls))
        self.assertTrue(any(c[:5] == ("nmcli", "device", "set", "wlan1", "managed") and c[-1] == "yes" for c in calls))

    def test_restore_command(self):
        with mock.patch.object(a.os, "geteuid", return_value=0), \
                mock.patch.object(a, "monitor_restore") as r:
            self.assertEqual(self.run_cmd(["--restore"])[0], 0)
        r.assert_called_once_with("wlan1")

    def test_it_never_transmits(self):
        src = inspect.getsource(a.monitor_run) + inspect.getsource(a.monitor_frame)
        for word in (".send(", "sendto", "sendall", "inject", "deauth"):
            self.assertNotIn(word, src)


class InstallTests(unittest.TestCase):
    def test_sudoers_has_exact_monitor_rules(self):
        text = a.build_sudoers("shane")
        self.assertIn(f"{a.INSTALLED_PATH} --wifi-listen --iface wlan1 --channel 6 --seconds {a.MONITOR_SECONDS} --json", text)
        self.assertIn(f"{a.INSTALLED_PATH} --wifi-listen --restore --iface wlan1", text)
        self.assertNotIn("--wifi-listen *", text)

    def test_install_adds_missing_packages(self):
        with mock.patch.object(a.os.path, "exists", return_value=False), \
                mock.patch.object(a.subprocess, "check_call") as run:
            self.assertTrue(a.ensure_system_packages(say=lambda *_: None))
        run.assert_called_once_with(["apt-get", "install", "-y", "iw"])

    def test_nothing_to_do_when_present(self):
        with mock.patch.object(a.os.path, "exists", return_value=True), \
                mock.patch.object(a.subprocess, "check_call") as run:
            self.assertTrue(a.ensure_system_packages())
        run.assert_not_called()

    def test_a_failed_install_is_not_fatal(self):
        import subprocess
        said = []
        with mock.patch.object(a.os.path, "exists", return_value=False), \
                mock.patch.object(a.subprocess, "check_call", side_effect=subprocess.CalledProcessError(100, "apt")):
            self.assertFalse(a.ensure_system_packages(say=said.append))
        self.assertIn("sudo apt install iw", " ".join(said))

    def test_install_self_calls_it(self):
        self.assertIn("ensure_system_packages()", inspect.getsource(a.install_self))


if __name__ == "__main__":
    unittest.main()
