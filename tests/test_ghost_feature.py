"""
The installer's optional Ghost hunt feature: off unless asked for, one switch file written as the user, removed
again on uninstall, and nothing else asked of the system. No network, no display, no root.

    python3 -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402

FACTS = {"aio_board": True, "internal_card": True, "flipper": True, "desktop": True}


def state(**changes):
    base = {"user": "u", "home": "/home/u", "app_source": "/home/u/aiov2_ctl", "old_checkout": False,
            "installed": set(), "unavailable": set(), "sudoers_listening": False, "present": set(),
            "wiki_pictures": {"count": 0, "bytes": 0}, "remote": None}
    base.update(changes)
    return base


class GhostFeatureTests(unittest.TestCase):
    def test_it_is_off_by_default_needs_the_app_and_installs_no_packages(self):
        row = a.feature_row("ghost")
        self.assertEqual(row["title"], "Ghost hunt (for fun)")
        self.assertFalse(row["default"])
        self.assertFalse(row["required"])
        self.assertEqual(row["apt"], ())
        self.assertEqual(row["needs"], ("app",))
        self.assertIn("entertainment", row["summary"])
        self.assertIn("real", row["summary"])
        self.assertEqual(a.resolve_features(["ghost"]), ["core", "app", "ghost"])

    def test_it_is_not_in_the_usual_set_but_is_in_all(self):
        everything_there = {"aio_board": True, "internal_card": True, "flipper": True, "desktop": True}
        self.assertNotIn("ghost", a.select_features([], everything_there))
        self.assertIn("ghost", a.select_features(["--all"], everything_there))

    def test_it_appears_in_the_feature_table_as_off(self):
        text = a.format_features(state(), FACTS)
        rows = {line.split()[0]: line.split() for line in text.splitlines() if line.split()[:1] == ["ghost"]}
        self.assertEqual(rows["ghost"][1], "off")
        self.assertIn("Ghost hunt (for fun)", text)

    def test_switching_it_on_is_one_step_done_as_the_user_and_says_what_it_does(self):
        plan = [s for s in a.install_plan(["ghost"], FACTS, state()) if s["feature"] == "ghost"]
        self.assertEqual([(s["id"], s["kind"], s["root"]) for s in plan], [("ghost.on", "ghost-on", False)])
        self.assertEqual(plan[0]["path"], "/home/u/.local/share/aiov2_ctl/ghost")
        for words in ("for fun", "installs no", "switches no rail", "never sent anywhere", "/enabled"):
            self.assertIn(words, plan[0]["detail"])
        self.assertIsNotNone(a.step_action(plan[0]))

    def test_without_a_user_it_stops_and_writes_nothing(self):
        plan = [s for s in a.install_plan(["ghost"], FACTS, state(user=None, home=None)) if s["feature"] == "ghost"]
        self.assertEqual([s["kind"] for s in plan], ["note"])
        self.assertTrue(plan[0].get("blocks"))

    def test_the_switch_is_written_as_the_user(self):
        seen = []

        def run(cmd, cwd=None, timeout=None):
            seen.append(cmd)
            return 0, "", ""
        with mock.patch.object(a.os, "geteuid", return_value=0), \
                mock.patch.object(a.os.path, "isfile", return_value=False):
            status, _ = a._act_ghost_on({"path": "/home/u/.local/share/aiov2_ctl/ghost"}, {"user": "u"}, run)
        self.assertEqual(status, "done")
        self.assertEqual(seen[0][:5], ["sudo", "-u", "u", "-H", "--"])
        self.assertIn("chmod 700", " ".join(seen[0]))
        self.assertEqual(a._act_ghost_on({"path": "/x"}, {"user": None}, run)[0], "failed")

    def test_it_counts_as_installed_when_the_switch_is_there(self):
        self.assertEqual(a.feature_installed("ghost", state(present={"ghost-on"})), "yes")
        self.assertNotEqual(a.feature_installed("ghost", state()), "yes")

    def test_removing_takes_only_the_switch_and_leaves_the_sessions(self):
        plan = a.uninstall_plan(["ghost"], state())
        self.assertEqual([s["id"] for s in plan], ["un.ghost.off"])
        self.assertEqual(plan[0]["kind"], "user-file")
        self.assertFalse(plan[0]["root"])
        self.assertEqual(plan[0]["paths"], ("/home/u/.local/share/aiov2_ctl/ghost/enabled",))
        self.assertIn("stay in", plan[0]["detail"])
        self.assertIn(plan[0]["kind"], a.UNINSTALL_ACTIONS)
        self.assertFalse(plan[0].get("units"))
        self.assertFalse(plan[0].get("trees"))
