"""
The installer's optional IGA browser feature: off unless asked for, its one package, a switch file and a
start-menu entry written as the user, both removed again on uninstall. No network, no display, no root.

    python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402
from ops_gui import launchers  # noqa: E402

FACTS = {"aio_board": True, "internal_card": True, "flipper": True, "desktop": True}


def state(**changes):
    base = {"user": "u", "home": "/home/u", "app_source": "/home/u/aiov2_ctl", "old_checkout": False,
            "installed": set(), "unavailable": set(), "sudoers_listening": False, "present": set(),
            "wiki_pictures": {"count": 0, "bytes": 0}, "remote": None}
    base.update(changes)
    return base


class BrowserFeatureTests(unittest.TestCase):
    def test_it_is_off_by_default_needs_the_app_and_installs_qt_webengine(self):
        row = a.feature_row("browser")
        self.assertEqual(row["title"], "IGA browser")
        self.assertFalse(row["default"])
        self.assertFalse(row["required"])
        self.assertEqual(row["apt"], ("python3-pyqt6.qtwebengine",))
        self.assertEqual(row["needs"], ("app",))
        self.assertIn("350 MB", row["summary"])
        self.assertEqual(a.resolve_features(["browser"]), ["core", "app", "browser"])

    def test_it_is_not_in_the_usual_set_but_is_in_all(self):
        self.assertNotIn("browser", a.select_features([], FACTS))
        self.assertIn("browser", a.select_features(["--all"], FACTS))

    def test_it_appears_in_the_feature_table_as_off(self):
        text = a.format_features(state(), FACTS)
        rows = {line.split()[0]: line.split() for line in text.splitlines() if line.split()[:1] == ["browser"]}
        self.assertEqual(rows["browser"][1], "off")
        self.assertIn("IGA browser", text)

    def test_switching_it_on_installs_the_package_then_one_step_as_the_user(self):
        plan = [s for s in a.install_plan(["browser"], FACTS, state()) if s["feature"] == "browser"]
        own = [s for s in plan if s["kind"] != "apt"]
        self.assertTrue(any(s["kind"] == "apt" and "python3-pyqt6.qtwebengine" in s["detail"] for s in plan))
        self.assertEqual([(s["id"], s["kind"], s["root"]) for s in own], [("browser.on", "browser-on", False)])
        self.assertEqual(own[0]["path"], "/home/u/.local/share/aiov2_ctl/iga")
        self.assertEqual(own[0]["source"], "/home/u/aiov2_ctl")
        for words in ("/enabled", "start-menu entry", "only the pages you ask for", "switches no rail"):
            self.assertIn(words, own[0]["detail"])
        self.assertIsNotNone(a.step_action(own[0]))

    def test_without_a_user_it_stops_and_writes_nothing(self):
        plan = [s for s in a.install_plan(["browser"], FACTS, state(user=None, home=None))
                if s["feature"] == "browser" and s["kind"] != "apt"]
        self.assertEqual([s["kind"] for s in plan], ["note"])
        self.assertTrue(plan[0].get("blocks"))

    def test_the_switch_and_the_launcher_are_written_as_the_user(self):
        seen = []

        def run(cmd, cwd=None, timeout=None):
            seen.append((cmd, cwd))
            return 0, "", ""
        with mock.patch.object(a.os, "geteuid", return_value=0), \
                mock.patch.object(a.os.path, "isfile", return_value=False), \
                mock.patch.object(a, "app_source", return_value="/home/u/aiov2_ctl"):
            status, _ = a._act_browser_on({"path": "/home/u/.local/share/aiov2_ctl/iga",
                                           "source": "/home/u/aiov2_ctl"}, {"user": "u"}, run)
        self.assertEqual(status, "done")
        self.assertEqual(len(seen), 2)
        for cmd, _ in seen:
            self.assertEqual(cmd[:5], ["sudo", "-u", "u", "-H", "--"])
        self.assertIn("chmod 700", " ".join(seen[0][0]))
        self.assertIn("--install-iga-launcher", seen[1][0])
        self.assertEqual(seen[1][1], "/home/u/aiov2_ctl")
        self.assertEqual(a._act_browser_on({"path": "/x"}, {"user": None}, run)[0], "failed")

    def test_it_counts_as_installed_only_with_its_switch_and_its_package(self):
        self.assertEqual(a.feature_installed("browser", state(present={"browser-on"},
                                                              installed={"python3-pyqt6.qtwebengine"})), "yes")
        self.assertNotEqual(a.feature_installed("browser", state(present={"browser-on"})), "yes")
        self.assertNotEqual(a.feature_installed("browser", state()), "yes")

    def test_removing_takes_the_switch_and_the_launcher_and_leaves_the_bookmarks(self):
        plan = a.uninstall_plan(["browser"], state())
        self.assertEqual([s["id"] for s in plan], ["un.browser.off"])
        self.assertEqual(plan[0]["kind"], "user-file")
        self.assertFalse(plan[0]["root"])
        self.assertEqual(plan[0]["paths"], ("/home/u/.local/share/aiov2_ctl/iga/enabled",
                                            "/home/u/.local/share/applications/iga-browser.desktop"))
        self.assertIn("stay in", plan[0]["detail"])
        self.assertIn(plan[0]["kind"], a.UNINSTALL_ACTIONS)
        self.assertFalse(plan[0].get("units"))
        self.assertFalse(plan[0].get("trees"))


class IgaLauncherTests(unittest.TestCase):
    def test_the_entry_opens_the_app_on_the_iga_tab(self):
        text = launchers.iga_entry("/r", isfile=lambda p: False)
        self.assertIn("Name=IGA browser\n", text)
        self.assertIn("-m ops_gui --tab IGA\n", text)
        self.assertIn("Icon=web-browser\n", text)
        self.assertIn("Icon=" + os.path.join(os.path.realpath("/r"), "img", "iga.png"),
                      launchers.iga_entry("/r", isfile=lambda p: True))

    def test_install_and_remove(self):
        with tempfile.TemporaryDirectory() as target:
            with mock.patch.object(launchers.os, "geteuid", return_value=1000):
                path = launchers.install_iga(REPO, target)
            self.assertEqual(os.path.basename(path), launchers.IGA_FILE)
            self.assertEqual(launchers.IGA_FILE, a.IGA_LAUNCHER_FILE)
            self.assertEqual(launchers.remove_iga(target), [path])
            self.assertEqual(launchers.remove_iga(target), [])

    def test_it_refuses_root(self):
        with mock.patch.object(launchers.os, "geteuid", return_value=0):
            with self.assertRaises(PermissionError):
                launchers.install_iga(REPO, "/nowhere")


if __name__ == "__main__":
    unittest.main()

