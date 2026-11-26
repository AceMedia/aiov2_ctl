import json
import os
import tempfile
import unittest

from ops_gui import bt_panel as bt

SNAP = {
    "t": 100.0, "adapter": "hci1", "mode": "extended",
    "devices": [
        {"addr": "11:22:33:44:55:66", "kind": "public", "name": "Kitchen Scale", "maker": "Acme", "services": ["weight scale"],
         "connectable": True, "rssi": -48, "best": -45, "count": 12, "first": 90.0, "last": 99.0},
        {"addr": "46:05:04:03:02:01", "kind": "private (changes)", "name": "", "maker": "Apple", "services": [],
         "connectable": True, "rssi": -70, "best": -66, "count": 3, "first": 95.0, "last": 40.0},
        {"addr": "c6:05:04:03:02:01", "kind": "random (fixed)", "name": "", "maker": "", "services": [],
         "connectable": False, "rssi": None, "best": None, "count": 1, "first": 99.0, "last": 99.5},
    ],
}


class CommandTests(unittest.TestCase):
    def test_the_command_is_exactly_what_the_sudoers_rule_allows(self):
        self.assertEqual(bt.scan_args(), ["sudo", "-n", "aiov2_ctl", "--bt-scan", "--adapter", "hci1",
                                          "--seconds", "600", "--json"])

    def test_it_only_ever_asks_for_a_scan(self):
        for word in ("connect", "pair", "advertis", "spam", "inject"):
            self.assertFalse(any(word in arg for arg in bt.scan_args()), word)

    def test_the_adapter_is_looked_for_in_sysfs(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(bt.adapter_available(root))
            os.mkdir(os.path.join(root, "hci1"))
            self.assertTrue(bt.adapter_available(root))


class ParseTests(unittest.TestCase):
    def test_a_snapshot_is_accepted(self):
        self.assertEqual(bt.parse_snapshot(json.dumps(SNAP)), SNAP)

    def test_anything_else_is_not(self):
        for line in ("", "not json", "[]", '{"devices": []}', '{"t": 1, "devices": "x"}',
                     '{"t": 1, "devices": [{"name": "no address"}]}', '{"t": 1, "devices": [7]}'):
            self.assertIsNone(bt.parse_snapshot(line), line)
        self.assertIsNone(bt.parse_snapshot(None))


class RowTests(unittest.TestCase):
    def test_rows_keep_the_listeners_order_and_fill_every_column(self):
        rows = bt.device_rows(SNAP)
        self.assertEqual([r["addr"] for r in rows], [d["addr"] for d in SNAP["devices"]])
        for row in rows:
            self.assertEqual(set(row), {key for _, key in bt.COLUMNS})
            self.assertTrue(all(isinstance(v, str) and v for v in row.values()))

    def test_a_named_device(self):
        row = bt.device_rows(SNAP)[0]
        self.assertEqual((row["name"], row["maker"], row["signal"], row["best"], row["count"], row["seen"], row["what"]),
                         ("Kitchen Scale", "Acme", "-48 dBm", "-45 dBm", "12", "now", "weight scale"))

    def test_missing_things_say_so_rather_than_guess(self):
        row = bt.device_rows(SNAP)[2]
        self.assertEqual((row["name"], row["maker"], row["signal"], row["best"], row["what"]),
                         (bt.NO_NAME, "-", "-", "-", "-"))

    def test_how_long_ago(self):
        self.assertEqual(bt.device_rows(SNAP)[1]["seen"], "60s ago")
        self.assertEqual(bt.ago(1000, 100), "15m ago")
        self.assertEqual(bt.ago(100, 105), "now")                          # a clock that went backwards

    def test_the_summary_counts_addresses_not_people(self):
        text = bt.summary(SNAP)
        self.assertEqual(text, "3 addresses heard: 1 with a name, 1 private (they change)")
        for word in ("people", "person", "phone", "nearby", "metres", "away"):
            self.assertNotIn(word, text)
        self.assertEqual(bt.summary({"devices": []}), "0 addresses heard")
        self.assertEqual(bt.summary({"devices": [{"addr": "x"}]}), "1 address heard")

    def test_no_column_claims_distance_or_direction(self):
        titles = " ".join(title for title, _ in bt.COLUMNS).lower()
        for word in ("distance", "direction", "bearing", "metres", "location", "person"):
            self.assertNotIn(word, titles)


class TabTests(unittest.TestCase):
    """Bluetooth is a tab of its own, straight after Wi-Fi, and only while its radio is there."""

    def test_it_sits_after_wifi_when_the_radio_is_there(self):
        from ops_gui import data
        names = data.tab_names({}, False, False, True, True, True)
        self.assertEqual(names[:5], ["Home", "Overview", "Wi-Fi", "Bluetooth", "Network"])
        self.assertEqual(names[-2:], ["Settings", "Help"])

    def test_no_tab_without_the_radio(self):
        from ops_gui import data
        self.assertNotIn("Bluetooth", data.tab_names({}, False))
        self.assertNotIn("Bluetooth", data.tab_names({}, True, True, True, True))

    def test_it_has_an_icon_at_both_sizes_and_in_help(self):
        from ops_gui import data, helpview
        for size in (32, 64):
            self.assertTrue(data.tab_icon_path("Bluetooth", size))
        art = helpview.TAB_ICONS["Bluetooth"]
        self.assertEqual(len(art), 16)
        self.assertTrue(all(len(row) == 16 for row in art))

    def test_it_is_no_longer_a_page_inside_wifi(self):
        import inspect
        from ops_gui import wifi_panel
        self.assertNotIn("bt_panel", inspect.getsource(wifi_panel))


class StopReasonTests(unittest.TestCase):
    """Why a scan ended by itself, in words."""

    def test_a_radio_that_restarted_is_said_so_not_broken_pipe(self):
        for error in ("Error: [Errno 32] Broken pipe", "BrokenPipeError: [Errno 32] Broken pipe",
                      "Error: [Errno 100] Network is down"):
            text = bt.stop_reason(error)
            self.assertIn("restarting itself", text)
            self.assertNotIn("Errno", text)
            self.assertTrue(bt.radio_restarted(error))

    def test_other_endings_are_not_taken_for_a_radio_restart(self):
        for error in ("", None, "sudo: a password is required", "Error: hci1 is busy: something else is scanning."):
            self.assertFalse(bt.radio_restarted(error))

    def test_a_restarted_radio_is_waited_for_then_the_scan_starts_again(self):
        self.assertEqual(bt.resume_step(1, 1.0, True), "wait")              # give it a moment even if it looks back
        self.assertEqual(bt.resume_step(1, bt.RESUME_WAIT, False), "wait")  # not back yet
        self.assertEqual(bt.resume_step(1, bt.RESUME_WAIT, True), "start")
        self.assertEqual(bt.resume_step(bt.RESUME_LIMIT, bt.RESUME_WAIT + 1, True), "start")

    def test_a_card_that_is_listed_but_not_yet_switched_on_is_not_ready(self):
        up = "hci1:\tType: Primary  Bus: USB\n\tBD Address: 38:7A:CC:84:8A:E8  ACL MTU: 1021:6\n\tUP RUNNING \n"
        down = "hci1:\tType: Primary  Bus: USB\n\tBD Address: 38:7A:CC:84:8A:E8  ACL MTU: 1021:6\n\tDOWN \n"
        self.assertTrue(bt.adapter_up(up))
        self.assertFalse(bt.adapter_up(down))
        self.assertFalse(bt.adapter_up(""))
        self.assertFalse(bt.adapter_up(None))

    def test_a_scan_that_is_logging_a_walk_starts_again_when_its_time_is_up(self):
        self.assertTrue(bt.scan_again("", True))
        self.assertTrue(bt.scan_again(None, True))

    def test_a_scan_nobody_is_logging_is_left_to_end(self):
        self.assertFalse(bt.scan_again("", False))

    def test_a_scan_that_ended_with_an_error_is_not_started_again_blindly(self):
        self.assertFalse(bt.scan_again("Error: hci1 is busy: something else is scanning.", True))
        self.assertFalse(bt.scan_again("sudo: a password is required", True))

    def test_a_radio_that_never_comes_back_or_keeps_restarting_stops_the_scan(self):
        self.assertEqual(bt.resume_step(1, bt.RESUME_GIVE_UP + 1, False), "stop")
        self.assertEqual(bt.resume_step(bt.RESUME_LIMIT + 1, bt.RESUME_WAIT, True), "stop")

    def test_sudo_refusing_points_at_the_one_command_that_fixes_it(self):
        for error in ("sudo: a password is required", "Sorry, user shane is not allowed to execute ..."):
            self.assertIn("--setup-sudoers", bt.stop_reason(error))

    def test_the_tools_own_messages_are_passed_on_without_the_error_prefix(self):
        self.assertEqual(bt.stop_reason("Error: hci1 is busy: something else is scanning."),
                         "hci1 is busy: something else is scanning.")
        self.assertEqual(bt.stop_reason("something odd"), "something odd")

    def test_nothing_printed_is_nothing_to_say(self):
        self.assertEqual(bt.stop_reason(""), "")
        self.assertEqual(bt.stop_reason(None), "")


if __name__ == "__main__":
    unittest.main()
