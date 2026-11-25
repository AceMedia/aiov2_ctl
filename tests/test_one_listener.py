import unittest

import aiov2_ctl as a


class OneListenerTests(unittest.TestCase):
    """Only one thing listens on a radio at a time."""

    def setUp(self):
        a._RADIO_OWNED.clear()
        self.addCleanup(a._RADIO_OWNED.clear)

    def test_the_first_listener_gets_the_radio_and_keeps_it(self):
        asked = []
        fd = a.own_radio("wlan1", lambda path, wait: asked.append((path, wait)) or 7)
        self.assertEqual(fd, 7)
        self.assertEqual(asked, [("radio-wlan1", 0)])
        self.assertEqual(a.own_radio("wlan1", lambda path, wait: self.fail("asked twice")), 7)

    def test_a_second_listener_is_refused_in_plain_words(self):
        def busy(path, wait):
            raise a.DeviceBusy("held")
        with self.assertRaises(OSError) as caught:
            a.own_radio("wlan1", busy)
        self.assertIn("already listening", str(caught.exception))
        self.assertIn("Survey or Sense", str(caught.exception))
        self.assertNotIn("wlan1", a._RADIO_OWNED)

    def test_two_radios_do_not_block_each_other(self):
        a.own_radio("wlan1", lambda path, wait: 1)
        self.assertEqual(a.own_radio("wlan0", lambda path, wait: 2), 2)

    def test_a_real_claim_blocks_a_second_one_and_is_dropped_when_let_go(self):
        first = a.acquire_device_lock("radio-test-only", wait=0)
        try:
            with self.assertRaises(a.DeviceBusy):
                a.acquire_device_lock("radio-test-only", wait=0)
        finally:
            a.release_device_lock(first)
        a.release_device_lock(a.acquire_device_lock("radio-test-only", wait=0))


class ServiceQuestionTests(unittest.TestCase):
    def test_asking_whether_a_service_is_enabled_never_uses_sudo(self):
        calls = []

        class Done:
            returncode = 1

        real = a.subprocess.run
        a.subprocess.run = lambda cmd, **kw: calls.append(cmd) or Done()
        try:
            self.assertFalse(a.GpioController._service_enabled("readsb.service"))
        finally:
            a.subprocess.run = real
        self.assertEqual(calls, [["systemctl", "is-enabled", "readsb.service"]])


if __name__ == "__main__":
    unittest.main()
