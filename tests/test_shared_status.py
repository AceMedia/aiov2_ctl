import json
import os
import tempfile
import unittest

import aiov2_ctl as a


class FreshnessTests(unittest.TestCase):
    """When one reading of the board can serve the next one who asks."""

    def test_a_recent_reading_is_used_again(self):
        self.assertTrue(a.status_cache_fresh(100.0, 101.5, None, 2.0))
        self.assertTrue(a.status_cache_fresh(100.0, 101.5, 99.0, 2.0))

    def test_an_old_reading_is_not(self):
        self.assertFalse(a.status_cache_fresh(100.0, 102.1, None, 2.0))

    def test_a_rail_switched_since_outdates_it_at_once(self):
        self.assertFalse(a.status_cache_fresh(100.0, 100.2, 100.1, 2.0))

    def test_nothing_kept_a_clock_gone_backwards_or_zero_age_means_read_again(self):
        self.assertFalse(a.status_cache_fresh(None, 100.0, None, 2.0))
        self.assertFalse(a.status_cache_fresh(100.0, 99.0, None, 2.0))
        self.assertFalse(a.status_cache_fresh(100.0, 100.0, None, 0))


class SharedStatusTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "x.status.json")
        self.mark = os.path.join(self.dir.name, "x.rails")
        self.reads = 0

    def build(self):
        self.reads += 1
        return {"rails": {"GPS": "off"}, "power": None, "companions": [], "n": self.reads}

    def ask(self, now, max_age=2.0):
        return a.shared_status(max_age, self.build, now, self.path, self.mark)

    def test_three_askers_within_two_seconds_cost_one_reading_of_the_board(self):
        first = self.ask(100.0)
        self.assertEqual(self.ask(100.5), first)
        self.assertEqual(self.ask(101.9), first)
        self.assertEqual(self.reads, 1)
        self.assertEqual(self.ask(102.5)["n"], 2)

    def test_a_switched_rail_makes_the_next_asker_read_the_board(self):
        self.ask(100.0)
        a.mark_rails_changed(self.mark)
        os.utime(self.mark, (100.4, 100.4))
        self.assertEqual(self.ask(100.5)["n"], 2)
        self.assertEqual(self.ask(100.9)["n"], 2)                 # and that newer reading is shared again

    def test_max_age_zero_always_reads_and_still_leaves_the_reading_for_others(self):
        self.ask(100.0, max_age=0)
        self.ask(100.1, max_age=0)
        self.assertEqual(self.reads, 2)
        self.assertEqual(self.ask(100.2)["n"], 2)

    def test_the_kept_reading_is_private(self):
        self.ask(100.0)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.listdir(self.dir.name), ["x.status.json"])

    def test_a_damaged_or_foreign_file_is_ignored_not_trusted(self):
        for text in ("not json", "[]", json.dumps({"written": 100.0, "status": {"no": "rails"}}),
                     json.dumps({"written": "soon", "status": {"rails": {}}})):
            with open(self.path, "w") as f:
                f.write(text)
            self.assertIsNone(a.read_shared_status(2.0, 100.1, self.path, self.mark), text)
        self.assertEqual(self.ask(100.2)["n"], 1)

    def test_a_folder_that_cannot_be_written_still_gives_a_status(self):
        got = a.shared_status(2.0, self.build, 100.0, "/proc/nope/x.status.json", self.mark)
        self.assertEqual(got["n"], 1)

    def test_the_reading_lives_beside_the_rails_mark(self):
        self.assertEqual(a.status_cache_path({}, 4242), "/tmp/aiov2_ctl-4242.status.json")


class AskingTests(unittest.TestCase):
    def test_status_reads_the_board_unless_told_an_age_it_may_reuse(self):
        self.assertEqual(a.status_max_age(["--json"]), 0.0)
        self.assertEqual(a.status_max_age(["--json", "--max-age", "5"]), 5.0)
        self.assertEqual(a.status_max_age(["--max-age"]), 0.0)
        self.assertEqual(a.status_max_age(["--max-age", "soon"]), 0.0)
        self.assertEqual(a.status_max_age(["--max-age", "-3"]), 0.0)

    def test_a_tray_menu_is_believed_open_for_a_minute_only(self):
        self.assertTrue(a.menu_counts_as_open(True, 100.0, 130.0))
        self.assertFalse(a.menu_counts_as_open(True, 100.0, 100.0 + a.MENU_OPEN_LIMIT + 1))
        self.assertFalse(a.menu_counts_as_open(False, 100.0, 101.0))
        self.assertFalse(a.menu_counts_as_open(True, None, 101.0))


if __name__ == "__main__":
    unittest.main()
