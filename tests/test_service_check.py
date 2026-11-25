"""
Tests for how often the tray asks systemd whether a service is running.

The tray redraws once a second while its menu or window is open. It used
to ask systemd about readsb and meshtasticd on every redraw, and to ask
again under sudo when the answer was no. These pin down the quieter rule.

    python3 -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402


class ServiceCheckDueTests(unittest.TestCase):
    def test_a_rail_that_is_off_is_not_asked_about_while_nothing_shows_it(self):
        self.assertFalse(a.service_check_due(False, False, None, 100.0))
        self.assertFalse(a.service_check_due(False, False, 0.0, 1000.0))

    def test_the_first_look_at_an_open_menu_asks_straight_away(self):
        self.assertTrue(a.service_check_due(False, True, None, 100.0))

    def test_a_rail_that_is_on_is_asked_about_even_with_the_menu_shut(self):
        self.assertTrue(a.service_check_due(True, False, None, 100.0))

    def test_an_answer_one_second_old_is_not_asked_for_again(self):
        self.assertFalse(a.service_check_due(True, True, 100.0, 101.0))
        self.assertFalse(a.service_check_due(False, True, 100.0, 109.9))

    def test_an_answer_ten_seconds_old_is_asked_for_again(self):
        self.assertTrue(a.service_check_due(True, True, 100.0, 110.0))
        self.assertTrue(a.service_check_due(False, True, 100.0, 110.0))

    def test_one_minute_of_an_open_menu_asks_six_times_not_sixty(self):
        asked, last = 0, None
        for second in range(60):
            if a.service_check_due(False, True, last, float(second)):
                asked += 1
                last = float(second)
        self.assertEqual(asked, 6)

    def test_opening_the_menu_always_gets_a_fresh_answer(self):
        self.assertTrue(a.service_check_due(False, False, 100.0, 100.5, forced=True))
        self.assertTrue(a.service_check_due(True, True, 100.0, 100.5, forced=True))

    def test_a_clock_that_went_backwards_asks_rather_than_waits(self):
        self.assertTrue(a.service_check_due(True, True, 500.0, 20.0))

    def test_the_wait_between_asks_is_ten_seconds(self):
        self.assertEqual(a.SERVICE_CHECK_INTERVAL_S, 10)


class ServiceActiveTests(unittest.TestCase):
    def run_with(self, returncode):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return mock.Mock(returncode=returncode)

        with mock.patch.object(a.subprocess, "run", side_effect=fake_run):
            answer = a.GpioController._service_active("readsb.service")
        return answer, calls

    def test_a_stopped_service_is_not_asked_about_again_under_sudo(self):
        answer, calls = self.run_with(3)
        self.assertFalse(answer)
        self.assertEqual(calls, [["systemctl", "is-active", "readsb.service"]])

    def test_a_running_service_is_reported_as_running(self):
        answer, calls = self.run_with(0)
        self.assertTrue(answer)
        self.assertEqual(calls, [["systemctl", "is-active", "readsb.service"]])


if __name__ == "__main__":
    unittest.main()
