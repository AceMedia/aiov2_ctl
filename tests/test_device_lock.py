"""
Tests for the serial device lock that makes concurrent aiov2_ctl commands queue.

    python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402


class DeviceLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_second_caller_waits_then_gets_it(self):
        first = a.acquire_device_lock("/dev/ttyACM1")
        got = []
        t = threading.Thread(target=lambda: got.append(a.acquire_device_lock("/dev/ttyACM1", wait=5)))
        t.start()
        time.sleep(0.4)
        self.assertEqual(got, [])           # still queued behind the first
        a.release_device_lock(first)
        t.join(5)
        self.assertEqual(len(got), 1)
        a.release_device_lock(got[0])

    def test_times_out_with_a_clear_message(self):
        first = a.acquire_device_lock("/dev/ttyACM1")
        try:
            with self.assertRaises(a.DeviceBusy) as cm:
                a.acquire_device_lock("/dev/ttyACM1", wait=0.3)
            self.assertIn("in use", str(cm.exception))
        finally:
            a.release_device_lock(first)

    def test_different_devices_do_not_block_each_other(self):
        one = a.acquire_device_lock("/dev/ttyACM0")
        two = a.acquire_device_lock("/dev/ttyACM1", wait=0.3)
        a.release_device_lock(one)
        a.release_device_lock(two)

    def test_lock_file_is_private(self):
        fd = a.acquire_device_lock("/dev/ttyACM1")
        path = os.path.join(self.tmp.name, "aiov2_ctl-ttyACM1.lock")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        a.release_device_lock(fd)

    def test_marauder_and_flipper_failures_release_the_lock(self):
        ser = a.MarauderSerial("/dev/ttyACM9")
        real_open = os.open

        def fake_open(path, flags, *rest):
            if str(path).startswith("/dev/"):
                raise OSError("no such device")
            return real_open(path, flags, *rest)

        with mock.patch.object(a.os, "open", side_effect=fake_open):
            with self.assertRaises(a.MarauderError):
                ser.open()
        again = a.acquire_device_lock("/dev/ttyACM9", wait=0.3)
        a.release_device_lock(again)


if __name__ == "__main__":
    unittest.main()
