import json
import os
import tempfile
import unittest

from ops_gui import wifi_panel as w

NETS = [
    {"ssid": "Home", "bssid": "aa:01", "channel": 11, "freq_mhz": 2462, "signal_dbm": -70, "security": "WPA2", "radio": "wlan0"},
    {"ssid": "Home", "bssid": "aa:01", "channel": 11, "freq_mhz": 2462, "signal_dbm": -60, "security": "WPA2", "radio": "wlan1"},
    {"ssid": "Home", "bssid": "aa:02", "channel": 108, "freq_mhz": 5540, "signal_dbm": -73, "security": "WPA2", "radio": "wlan1"},
    {"ssid": "Cafe", "bssid": "bb:01", "channel": 1, "freq_mhz": 2412, "signal_dbm": -80, "security": "Open", "radio": "wlan1"},
    {"ssid": "", "bssid": "", "channel": 6, "signal_dbm": None, "security": "WPA2", "radio": "flipper-marauder"},
    {"ssid": "Cafe2", "bssid": "bb:02", "channel": 1, "freq_mhz": 2412, "signal_dbm": -50, "security": "WPA3", "radio": "wlan0"},
]


class CommandTests(unittest.TestCase):
    def test_args(self):
        self.assertEqual(w.scan_args(), ["aiov2_ctl", "--wifi", "scan", "--json"])
        self.assertEqual(w.scan_args("wlan1", True)[-3:], ["--iface", "wlan1", "--save"])

    def test_odd_radio_names_are_dropped(self):
        for bad in ("wlan0; rm -rf /", "../x", "a b", "x" * 40):
            self.assertNotIn("--iface", w.scan_args(bad))
        self.assertIn("--iface", w.scan_args("flipper-marauder"))

    def test_nothing_active(self):
        import inspect
        src = inspect.getsource(w).lower()
        for word in ("deauth", "inject", "monitor mode", "airmon", "nmcli dev wifi connect"):
            self.assertNotIn(word, src)


class FilterTests(unittest.TestCase):
    def test_filters(self):
        self.assertEqual(len(w.filter_networks(NETS)), 6)
        self.assertEqual(len(w.filter_networks(NETS, radio="wlan1")), 3)
        self.assertEqual(len(w.filter_networks(NETS, band="5")), 1)
        self.assertEqual(len(w.filter_networks(NETS, band="2.4")), 5)
        self.assertEqual(len(w.filter_networks(NETS, text="CAFE")), 2)
        self.assertEqual(len(w.filter_networks(NETS, text="aa:02")), 1)
        self.assertEqual(w.filter_networks(None), [])
        self.assertEqual(w.filter_networks([5, None, "x"]), [])

    def test_band(self):
        self.assertEqual(w.band_of({"freq_mhz": 2412}), "2.4")
        self.assertEqual(w.band_of({"freq_mhz": 5540}), "5")
        self.assertEqual(w.band_of({"freq_mhz": 6115}), "6")
        self.assertEqual(w.band_of({"channel": 6}), "2.4")
        self.assertEqual(w.band_of({"channel": 40}), "5")


class RowTests(unittest.TestCase):
    def test_merge_and_order(self):
        rows = w.merged_rows(NETS)
        self.assertEqual(len(rows), 5)                                  # Home 2.4 merged across radios
        self.assertEqual([r["ssid"] for r in rows][:2], ["Cafe2", "Home"])   # strongest first
        home = next(r for r in rows if r["bssid"] == "aa:01")
        self.assertEqual(home["heard"], "wlan1 -60 | wlan0 -70")
        self.assertIn("-60 dBm", home["signal"])

    def test_hidden_open_and_no_signal(self):
        rows = w.merged_rows(NETS)
        hidden = next(r for r in rows if r["channel"] == "6")
        self.assertEqual((hidden["ssid"], hidden["signal"], hidden["heard"]), ("(hidden)", "-", "flipper-marauder"))
        self.assertTrue(next(r for r in rows if r["ssid"] == "Cafe")["open"])
        self.assertEqual(rows[-1]["dbm"], -999)

    def test_summary(self):
        scan = {"radios": [{"name": "wlan0", "up": True}, {"name": "wlan1", "up": False}]}
        self.assertEqual(w.summary_line(scan, w.merged_rows(NETS)), "5 networks  |  scanned with wlan0  |  1 open")
        self.assertIn("no radio up", w.summary_line({}, []))

    def test_bars(self):
        self.assertEqual(w.bars(-50), "####")
        self.assertEqual(w.bars(-90), "----")
        self.assertEqual(w.bars(None), "----")


class ChannelTests(unittest.TestCase):
    def test_chart(self):
        two = w.channel_chart(NETS, "2.4")
        self.assertEqual([c for c, _, _ in two], list(range(1, 14)))
        self.assertEqual(dict((c, n) for c, n, _ in two)[1], 2)
        self.assertEqual(dict((c, s) for c, _, s in two)[1], -50)
        self.assertIsNone(dict((c, s) for c, _, s in two)[3])
        five = dict((c, n) for c, n, _ in w.channel_chart(NETS, "5"))
        self.assertEqual(five[108], 1)
        self.assertEqual(w.channel_chart(NETS, "6"), [])

    def test_advice(self):
        lines = w.channel_advice(NETS)
        self.assertTrue(lines[0].startswith("Quietest 2.4 GHz choice: channel"))
        self.assertIn("Empty 5 GHz channels: 36", lines[1])
        self.assertEqual(w.channel_advice([]), ["Scan first to see the channels."])

    def test_quietest_is_really_quiet(self):
        crowded = [{"channel": c, "freq_mhz": 2407 + 5 * c, "signal_dbm": -60} for c in (1, 1, 2, 11, 11, 10)]
        self.assertIn("channel 6", w.channel_advice(crowded)[0])


class SavedTests(unittest.TestCase):
    def test_saved_scans_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("wifi-scan-20261123-233207.json", "wifi-scan-20261124-101500.json", "other.json"):
                with open(os.path.join(d, name), "w") as f:
                    json.dump({"networks": []}, f)
            items = w.saved_scans(d)
            self.assertEqual([label for _, label in items], ["24 Nov 2026 10:15:00", "23 Nov 2026 23:32:07"])
            self.assertEqual(w.load_scan(items[0][0]), {"networks": []})

    def test_bad_files_are_safe(self):
        with tempfile.TemporaryDirectory() as d:
            for name, body in (("a.json", "not json"), ("b.json", "[1,2]"), ("c.json", '{"x": 1}')):
                p = os.path.join(d, name)
                with open(p, "w") as f:
                    f.write(body)
                self.assertEqual(w.load_scan(p), {})
            self.assertEqual(w.load_scan(os.path.join(d, "none.json")), {})
            self.assertEqual(w.saved_scans(os.path.join(d, "missing")), [])

    def test_radio_text(self):
        self.assertEqual(w.radio_text({"name": "wlan1", "kind": "internal-usb", "driver": "mt7921u", "up": True}),
                         "wlan1   internal usb   mt7921u   up")
        self.assertEqual(w.radio_names({"radios": [{"name": "a"}, {}, 5]}), ["a"])


if __name__ == "__main__":
    unittest.main()
