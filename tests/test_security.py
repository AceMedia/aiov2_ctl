"""
Tests for the privilege escalation guard and the generated sudoers rules.

    python3 -m unittest discover -s tests
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402

VISUDO = shutil.which("visudo", path=os.environ.get("PATH", "") + ":/usr/sbin:/sbin")


class TrustedFileTests(unittest.TestCase):
    def test_user_writable_file_is_not_trusted(self):
        with tempfile.NamedTemporaryFile() as f:
            # Owned by the test user, so only trusted if that user is root
            self.assertEqual(a.is_trusted_file(f.name), os.geteuid() == 0)

    def test_missing_file_is_not_trusted(self):
        self.assertFalse(a.is_trusted_file("/nonexistent/aiov2_ctl"))


class RerunWithSudoTests(unittest.TestCase):
    def test_refuses_untrusted_script(self):
        with mock.patch.object(a, "is_trusted_file", return_value=False), \
             mock.patch.object(a.os, "execvp") as ex:
            with self.assertRaises(SystemExit) as cm:
                a.rerun_with_sudo(["--boot-rail", "GPS", "on"])
        self.assertEqual(cm.exception.code, 1)
        ex.assert_not_called()

    def test_allows_untrusted_when_explicitly_confirmed(self):
        with mock.patch.object(a, "is_trusted_file", return_value=False), \
             mock.patch.object(a.os, "execvp") as ex:
            a.rerun_with_sudo(["--install"], allow_untrusted=True)
        self.assertEqual(ex.call_args[0][1][0], "sudo")
        self.assertIn("--install", ex.call_args[0][1])

    def test_prefers_installed_copy(self):
        with mock.patch.object(a, "is_trusted_file", return_value=True), \
             mock.patch.object(a.os, "execvp") as ex:
            a.rerun_with_sudo(["--boot-rail", "SDR", "off"])
        self.assertIn(a.INSTALLED_PATH, ex.call_args[0][1])


class SudoersTests(unittest.TestCase):
    def test_rules_are_exact_with_no_wildcards(self):
        text = a.build_sudoers("shane")
        self.assertNotIn("*", text.replace("# ", ""))
        self.assertIn(f"{a.INSTALLED_PATH} --boot-rail GPS on", text)
        self.assertIn(f"{a.INSTALLED_PATH} --boot-device nvme", text)
        self.assertNotIn("--update", text)
        self.assertNotIn("--install", text)

    def test_every_rail_and_state_covered(self):
        text = a.build_sudoers("shane")
        for feature in a.GPIO_MAP:
            for state in ("on", "off"):
                self.assertIn(f"--boot-rail {feature} {state}", text)

    @unittest.skipUnless(VISUDO, "visudo not installed")
    def test_visudo_accepts_generated_rules(self):
        with tempfile.NamedTemporaryFile("w", suffix=".sudoers", delete=False) as f:
            f.write(a.build_sudoers("shane"))
            path = f.name
        try:
            rc = subprocess.call([VISUDO, "-cf", path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.assertEqual(rc, 0)
        finally:
            os.remove(path)

    def test_rejects_unsafe_username(self):
        for bad in ("root; rm", "a b", "", "UPPER", "x\nALL ALL=(ALL) NOPASSWD: ALL"):
            self.assertIsNone(a._USERNAME_RE.match(bad), bad)

    def test_setup_requires_root(self):
        with mock.patch.object(a.os, "geteuid", return_value=1000):
            self.assertEqual(a.setup_sudoers(), 1)


if __name__ == "__main__":
    unittest.main()
