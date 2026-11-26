import os
import tempfile
import unittest
from unittest import mock

from ops_gui import launchers as l


def fake_repo(root):
    os.makedirs(os.path.join(root, "ops_gui"))
    open(os.path.join(root, "ops.py"), "w").close()
    return root


class LauncherTests(unittest.TestCase):
    def test_entries_point_at_the_checkout(self):
        e = l.entries("/home/shane/ops-agentic")
        gui, cli = e[l.GUI_FILE], e[l.CLI_FILE]
        self.assertIn("Exec=/usr/bin/python3 -m ops_gui", gui)
        self.assertIn("Path=/home/shane/ops-agentic", gui)
        self.assertIn("Terminal=false", gui)
        self.assertIn("Exec=/usr/bin/python3 /home/shane/ops-agentic/ops.py", cli)
        self.assertIn("Terminal=true", cli)
        for text in e.values():
            self.assertTrue(text.startswith("[Desktop Entry]\nType=Application\n"))
            self.assertIn("Categories=System;Utility;", text)

    def test_paths_with_spaces_are_quoted(self):
        self.assertEqual(l.quote("/a b/ops.py"), '"/a b/ops.py"')
        self.assertEqual(l.quote("/a/ops.py"), "/a/ops.py")
        self.assertIn('"/x y/ops.py"', l.entries("/x y")[l.CLI_FILE])

    def test_install_and_remove(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as menu:
            fake_repo(repo)
            with mock.patch.object(l.os, "geteuid", return_value=1000):
                written = l.install(repo, menu)
                self.assertEqual(sorted(os.path.basename(p) for p in written), sorted([l.GUI_FILE, l.CLI_FILE]))
                self.assertTrue(all(os.access(p, os.X_OK) for p in written))
                self.assertEqual(len(l.remove(menu)), 2)
                self.assertEqual(os.listdir(menu), [])
                self.assertEqual(l.remove(menu), [])

    def test_it_refuses_root_and_the_wrong_folder(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as menu:
            fake_repo(repo)
            with mock.patch.object(l.os, "geteuid", return_value=0):
                with self.assertRaises(PermissionError):
                    l.install(repo, menu)
            with mock.patch.object(l.os, "geteuid", return_value=1000):
                with self.assertRaises(FileNotFoundError):
                    l.install(menu, menu)
            self.assertEqual(os.listdir(menu), [])

    def test_menu_folder_follows_xdg(self):
        self.assertEqual(l.applications_dir({"XDG_DATA_HOME": "/x"}), "/x/applications")
        self.assertTrue(l.applications_dir({}).endswith(".local/share/applications"))

    def test_command_ignores_other_arguments(self):
        self.assertIsNone(l.command(["--tab", "Sense"]))


if __name__ == "__main__":
    unittest.main()
