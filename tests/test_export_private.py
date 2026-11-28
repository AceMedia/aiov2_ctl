"""History exports: private to the user, and never overwriting one another."""
import datetime as real_datetime
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_agent import preferences  # noqa: E402


class FrozenClock(real_datetime.datetime):
    """Every call returns the same instant, so two exports fall in the same second (and microsecond)."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 28, 12, 0, 0, 0)

    @classmethod
    def utcnow(cls):
        return cls(2026, 11, 28, 12, 0, 0, 0)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_file_is_private_and_folder_made_private(self):
        folder = os.path.join(self.tmp.name, "exports")
        path = preferences.export_telemetry_snapshot({"node_info": {}}, {"lat": 1, "lon": 2}, folder=folder)
        self.assertTrue(path.startswith(folder))
        self.assertEqual(mode(path), 0o600)
        self.assertEqual(mode(folder), 0o700)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["telemetry"], {"node_info": {}})

    def test_an_existing_folder_is_left_alone(self):
        os.chmod(self.tmp.name, 0o755)
        preferences.export_telemetry_snapshot({}, folder=self.tmp.name)
        self.assertEqual(mode(self.tmp.name), 0o755)

    def test_two_exports_in_the_same_second_get_two_files(self):
        with mock.patch.object(preferences, "datetime", FrozenClock):
            a = preferences.export_telemetry_snapshot({"n": 1}, folder=self.tmp.name)
            b = preferences.export_telemetry_snapshot({"n": 2}, folder=self.tmp.name)
        self.assertNotEqual(a, b)
        self.assertEqual(len(os.listdir(self.tmp.name)), 2)
        for path, n in ((a, 1), (b, 2)):
            self.assertEqual(mode(path), 0o600)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["telemetry"], {"n": n})

    def test_not_a_snapshot(self):
        self.assertIsNone(preferences.export_telemetry_snapshot(None, folder=self.tmp.name))
        self.assertEqual(os.listdir(self.tmp.name), [])


if __name__ == "__main__":
    unittest.main()
