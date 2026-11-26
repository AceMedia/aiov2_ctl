import subprocess
import tempfile
import time
import unittest
from unittest import mock

from ops_agent.sdr import classify_rtl_error, terminate_process

try:
    from .sdr_helpers import FakeProc, make_sdr
except ImportError:
    from sdr_helpers import FakeProc, make_sdr


class ClassifyTests(unittest.TestCase):
    def test_missing(self):
        kind, msg = classify_rtl_error("No supported devices found.")
        self.assertEqual(kind, "missing")
        self.assertIn("power rail", msg)

    def test_claimed_by_dvb(self):
        kind, msg = classify_rtl_error("usb_claim_interface error -6", dvb_loaded=True)
        self.assertEqual(kind, "dvb")
        self.assertIn("dvb_usb_rtl28xxu", msg)

    def test_busy_without_dvb(self):
        kind, _ = classify_rtl_error("usb_claim_interface error -6", dvb_loaded=False)
        self.assertEqual(kind, "busy")

    def test_permission_and_dropout(self):
        self.assertEqual(classify_rtl_error("usb_open error -3")[0], "permission")
        self.assertEqual(classify_rtl_error("Synchronous read failed")[0], "dropout")

    def test_clean_output(self):
        self.assertEqual(classify_rtl_error("Found 1 device(s):\nUsing device 0"), (None, None))
        self.assertEqual(classify_rtl_error(None), (None, None))


class InitStateTests(unittest.TestCase):
    def test_ready(self):
        s = make_sdr()
        self.assertTrue(s.active)

    def test_missing_dongle_is_inactive_with_reason(self):
        s = make_sdr(stderr="No supported devices found.")
        self.assertFalse(s.active)
        self.assertIn("No RTL-SDR", s.get_status())

    def test_tools_missing(self):
        s = make_sdr(side_effect=FileNotFoundError())
        self.assertFalse(s.active)
        self.assertIn("apt install rtl-sdr", s.status_reason)

    def test_timeout(self):
        s = make_sdr(side_effect=subprocess.TimeoutExpired("rtl_test", 1))
        self.assertFalse(s.active)


class CleanupTests(unittest.TestCase):
    def test_terminate_process_reaps_running_child(self):
        p = FakeProc()
        terminate_process(p)
        self.assertTrue(p.terminated and p.waited)
        p.stdout.close.assert_called()

    def test_terminate_process_reaps_exited_child(self):
        p = FakeProc(rc=1)
        terminate_process(p)
        self.assertFalse(p.terminated)
        self.assertTrue(p.waited)

    def test_terminate_escalates_to_kill(self):
        p = FakeProc()
        calls = []

        def wait(timeout=None):
            calls.append(timeout)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired("x", timeout)
            return -9

        p.wait = wait
        terminate_process(p)
        self.assertTrue(p.killed)

    def test_terminate_none_is_fine(self):
        terminate_process(None)

    def test_shutdown_is_idempotent_and_stops_children(self):
        s = make_sdr()
        pan = FakeProc()
        s.panadapter_proc = pan
        s.shutdown()
        s.shutdown()
        self.assertTrue(pan.terminated)
        self.assertIsNone(s.panadapter_proc)
        self.assertFalse(s.scan_enabled)

    def test_stop_panadapter_closes_stderr_file(self):
        s = make_sdr()
        s.panadapter_proc = FakeProc()
        s._pan_stderr = tempfile.TemporaryFile()
        handle = s._pan_stderr
        s.stop_panadapter()
        self.assertTrue(handle.closed)
        self.assertIsNone(s._pan_stderr)


class DropoutTests(unittest.TestCase):
    def dead_proc_sdr(self, stderr, lived=0.5):
        s = make_sdr()
        s.panadapter_proc = FakeProc(rc=1)
        s.panadapter_start_time = time.time() - lived
        s._pan_stderr = tempfile.TemporaryFile()
        s._pan_stderr.write(stderr.encode())
        s.spectrum = [1.0, 2.0]
        return s

    def test_exit_is_noticed_and_retried_with_backoff(self):
        s = self.dead_proc_sdr("Synchronous read failed")
        s.read_panadapter()
        self.assertEqual(s.link_state, "retrying")
        self.assertEqual(s.device_failures, 1)
        self.assertIn("dropped out", s.last_error)
        self.assertEqual(s.spectrum, [])
        self.assertGreater(s.next_restart_at, time.time())
        self.assertFalse(s._restart_allowed())

    def test_restart_after_backoff(self):
        s = self.dead_proc_sdr("Synchronous read failed")
        s.read_panadapter()
        s.next_restart_at = 0
        with mock.patch.object(s, "start_panadapter") as start:
            s.read_panadapter()
        start.assert_called_once()

    def test_gives_up_after_repeated_fast_failures(self):
        s = make_sdr()
        for _ in range(s.max_device_failures):
            s.panadapter_proc = FakeProc(rc=1)
            s.panadapter_start_time = time.time()
            s._handle_panadapter_exit()
        self.assertEqual(s.link_state, "lost")
        self.assertFalse(s._restart_allowed())

    def test_missing_dongle_gives_up_at_once(self):
        s = self.dead_proc_sdr("No supported devices found.")
        s.read_panadapter()
        self.assertEqual(s.link_state, "lost")
        self.assertIn("No RTL-SDR", s.get_status())

    def test_retune_allows_a_fresh_attempt(self):
        s = self.dead_proc_sdr("No supported devices found.")
        s.read_panadapter()
        s.request_tune(100.0)
        self.assertEqual(s.link_state, "retrying")
        self.assertEqual(s.device_failures, 0)

    def test_long_run_then_drop_counts_as_first_failure(self):
        s = self.dead_proc_sdr("", lived=60)
        s.device_failures = 4
        s.read_panadapter()
        self.assertEqual(s.device_failures, 1)


class AudioHealthTests(unittest.TestCase):
    def test_dead_player_turns_audio_off(self):
        s = make_sdr()
        s.audio_enabled = True
        s.audio_thread = mock.Mock()
        s.audio_thread.is_alive.return_value = True
        s.paplay_proc = FakeProc(rc=1)
        s._check_audio_health()
        self.assertFalse(s.audio_enabled)
        self.assertIn("paplay", s.audio_error)

    def test_healthy_audio_untouched(self):
        s = make_sdr()
        s.audio_enabled = True
        s.audio_thread = mock.Mock()
        s.audio_thread.is_alive.return_value = True
        s.paplay_proc = FakeProc()
        s._check_audio_health()
        self.assertTrue(s.audio_enabled)


if __name__ == "__main__":
    unittest.main()
