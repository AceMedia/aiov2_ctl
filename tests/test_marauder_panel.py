import unittest

from ops_gui import data, marauder_panel as m


class ParseTests(unittest.TestCase):
    def test_ap_line(self):
        self.assertEqual(m.parse_ap_line("[0][CH:6] HomeNet -50"),
                         {"index": 0, "channel": 6, "ssid": "HomeNet", "rssi": -50})
        self.assertEqual(m.parse_ap_line("[12][CH: 11] My Cafe Wifi -71")["ssid"], "My Cafe Wifi")

    def test_noise_is_ignored(self):
        for line in ("", None, "Starting scan", "Saved to /x", "[0] nothing"):
            self.assertIsNone(m.parse_ap_line(line))

    def test_hidden_and_no_rssi(self):
        ap = m.parse_ap_line("[3][CH:1]")
        self.assertEqual((ap["ssid"], ap["rssi"]), ("", None))

    def test_a_name_ending_in_something_rssi_shaped_is_not_misread(self):
        # a dash-suffixed SSID with no real RSSI on the line must not be split into a shorter SSID plus a signal
        ap = m.parse_ap_line("[1][CH:6] Home-2")
        self.assertEqual((ap["ssid"], ap["rssi"]), ("Home-2", None))
        ap2 = m.parse_ap_line("[1][CH:6] Home-2 -50")
        self.assertEqual((ap2["ssid"], ap2["rssi"]), ("Home-2", -50))

    def test_strongest_first_and_deduped(self):
        aps = m.parse_aps(["[0][CH:6] A -80", "[1][CH:1] B -40", "[0][CH:6] A -60", "junk"])
        self.assertEqual([a["ssid"] for a in aps], ["B", "A"])
        self.assertEqual(aps[1]["rssi"], -60)

    def test_rows_and_summary(self):
        aps = m.parse_aps(["[0][CH:6] A -80", "[1][CH:6] B -40", "[2][CH:1]"])
        rows = m.ap_rows(aps)
        self.assertEqual(rows[0], {"ssid": "B", "channel": "6", "signal": "-40 dBm"})
        self.assertEqual(rows[-1]["ssid"], "<hidden>")
        self.assertEqual(rows[-1]["signal"], "-")
        self.assertIn("Busiest channel 6 (2)", m.summary(aps))
        self.assertEqual(m.summary([]), "Nothing heard yet.")
        self.assertEqual(m.summary([], "probe", 12), "12 lines heard.")
        self.assertEqual(m.summary([], "probe", 0), "Nothing heard.")


class CommandTests(unittest.TestCase):
    def test_scan_args_are_passive_cli_only(self):
        self.assertEqual(m.scan_args("beacon", 60),
                         ["aiov2_ctl", "--marauder", "scan", "beacon", "--seconds", "60"])

    def test_unknown_mode_falls_back(self):
        self.assertEqual(m.scan_args("attack", 30)[3], "all")
        self.assertEqual(m.scan_args("deauth", 30)[3], "all")

    def test_seconds_are_clamped(self):
        self.assertEqual(m.clamp_seconds(0), 1)
        self.assertEqual(m.clamp_seconds(99999), 3600)
        self.assertEqual(m.clamp_seconds("x"), 30)

    def test_only_passive_words_in_source(self):
        import inspect
        src = inspect.getsource(m).lower()
        for word in ("attack", "deauth ", "blespam", "spoof", "evilportal", "sniffpmkid"):
            self.assertNotIn(word, src.replace("no attack, spoofing or portal", ""))

    def test_report_lines(self):
        self.assertEqual(m.saved_path(["x", "Saved to /home/u/cap.log (private, mode 600)."]), "/home/u/cap.log")
        self.assertEqual(m.saved_path([]), "")
        self.assertEqual(m.failure_text(["Error: No Marauder board found."]), "No Marauder board found.")
        self.assertEqual(m.failure_text(["fine"]), "")


class TabTests(unittest.TestCase):
    def test_marauder_tab_after_wifi_only_when_shown(self):
        self.assertNotIn("Marauder", data.tab_names({}, False))
        names = data.tab_names({}, True, True)
        self.assertEqual(names[:3], ["Overview", "Wi-Fi", "Marauder"])

    def test_present(self):
        self.assertTrue(data.marauder_present({"radios": [{"kind": "flipper-marauder"}]}))
        self.assertFalse(data.marauder_present({"radios": [{"kind": "internal-usb"}]}))
        self.assertFalse(data.marauder_present({}))

    def test_icon_exists(self):
        self.assertTrue(data.tab_icon_path("Marauder", 32))


class EnvTests(unittest.TestCase):
    def test_output_is_unbuffered_so_the_tab_updates_live(self):
        env = m.popen_env({"PATH": "/usr/bin"})
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")
        self.assertEqual(env["PATH"], "/usr/bin")


class ModeTests(unittest.TestCase):
    def test_every_mode_is_a_listen_only_one_and_builds_a_safe_command(self):
        banned = ("attack", "spam", "portal", "pmkid", "eapol", "handshake", "deauth", "spoof", "karma", "rick")
        for key, label in m.MODES:
            self.assertEqual(m.scan_args(key, 30)[:4], ["aiov2_ctl", "--marauder", "scan", key])
            self.assertFalse(key in banned, key)
        self.assertNotIn("deauth", [k for k, _ in m.MODES])          # only the detector, deauthwatch, exists
        self.assertEqual(m.scan_args("attack", 30)[3], "all")
        self.assertEqual(len({k for k, _ in m.MODES}), len(m.MODES))
        self.assertTrue(set(m.NETWORK_MODES) <= {k for k, _ in m.MODES})


if __name__ == "__main__":
    unittest.main()

