import os
import tempfile
import unittest

from ops_gui import registry as r


class NormaliseTests(unittest.TestCase):
    def test_valid_forms(self):
        self.assertEqual(r.normalise("AA:BB:CC:00:11:22"), "aa:bb:cc:00:11:22")
        self.assertEqual(r.normalise(" aa:bb:cc:00:11:22 "), "aa:bb:cc:00:11:22")

    def test_rubbish_is_refused(self):
        for bad in ("", None, "not a mac", "aa:bb:cc:00:11", "aa:bb:cc:00:11:22:33", "aabbcc001122", 5):
            self.assertEqual(r.normalise(bad), "")

    def test_short_label_matches_the_old_sense_style(self):
        self.assertEqual(r.short_label("aa:bb:cc:00:11:22"), "ap " + "aa:bb:cc:00:11:22"[-8:])
        self.assertEqual(r.short_label("not a mac"), "ap ?")


class SeeTests(unittest.TestCase):
    def test_first_sighting_is_new_the_rest_are_not(self):
        reg = r.Registry()
        self.assertTrue(reg.see("AA:BB:CC:00:11:22", ssid="Home", channel=6, security="WPA2", rssi=-50))
        self.assertFalse(reg.see("aa:bb:cc:00:11:22", rssi=-48))
        self.assertFalse(r.Registry().see(""))
        self.assertFalse(r.Registry().see(None))

    def test_facts_accumulate_and_later_readings_update_them(self):
        reg = r.Registry()
        reg.see("aa:bb:cc:00:11:22", ssid="Home", channel=6, security="WPA2", rssi=-50, source="survey")
        reg.see("aa:bb:cc:00:11:22", rssi=-40, source="wifi")
        e = reg.known("aa:bb:cc:00:11:22")
        self.assertEqual((e["ssid"], e["channel"], e["security"], e["rssi"]), ("Home", 6, "WPA2", -40))
        self.assertEqual(sorted(e["sources"]), ["survey", "wifi"])

    def test_a_blank_reading_never_erases_what_is_already_known(self):
        reg = r.Registry()
        reg.see("aa:bb:cc:00:11:22", ssid="Home", channel=6, security="WPA2")
        reg.see("aa:bb:cc:00:11:22", ssid="", channel=None, security="")
        e = reg.known("aa:bb:cc:00:11:22")
        self.assertEqual((e["ssid"], e["channel"], e["security"]), ("Home", 6, "WPA2"))

    def test_known_is_none_for_an_unseen_or_bad_address(self):
        reg = r.Registry()
        self.assertIsNone(reg.known("aa:bb:cc:00:11:22"))
        self.assertIsNone(reg.known("rubbish"))


class FeedTests(unittest.TestCase):
    SURVEY_SNAP = {"t": 100.0, "aps": [
        {"bssid": "aa:bb:cc:00:11:22", "ssid": "Home", "channel": 6, "security": "WPA2", "rssi": -50.0},
        {"bssid": "not-a-real-mac", "ssid": "Junk"},
        "rubbish",
    ]}
    WIFI_NETWORKS = [
        {"bssid": "aa:bb:cc:00:11:33", "ssid": "Cafe", "channel": 36, "security": "Open", "signal_dbm": -70},
        {"ssid": "no bssid at all"},
    ]

    def test_see_survey_feeds_real_aps_only(self):
        reg = r.Registry()
        new = reg.see_survey(self.SURVEY_SNAP)
        self.assertEqual(new, ["aa:bb:cc:00:11:22"])
        self.assertEqual(reg.known("aa:bb:cc:00:11:22")["ssid"], "Home")
        self.assertIsNone(reg.known("not-a-real-mac"))

    def test_see_wifi_scan(self):
        reg = r.Registry()
        new = reg.see_wifi_scan(self.WIFI_NETWORKS)
        self.assertEqual(new, ["aa:bb:cc:00:11:33"])
        e = reg.known("aa:bb:cc:00:11:33")
        self.assertEqual((e["ssid"], e["security"], e["rssi"]), ("Cafe", "Open", -70))

    def test_feeds_from_both_sources_merge_into_one_entry(self):
        reg = r.Registry()
        reg.see_survey(self.SURVEY_SNAP)
        reg.see_wifi_scan([{"bssid": "aa:bb:cc:00:11:22", "signal_dbm": -45}])
        e = reg.known("aa:bb:cc:00:11:22")
        self.assertEqual(e["ssid"], "Home")            # still the name survey gave it
        self.assertEqual(e["rssi"], -45)                # refreshed by the later wifi scan reading
        self.assertEqual(sorted(e["sources"]), ["survey", "wifi"])

    def test_malformed_input_never_raises(self):
        reg = r.Registry()
        self.assertEqual(reg.see_survey({}), [])
        self.assertEqual(reg.see_survey(None), [])
        self.assertEqual(reg.see_survey({"aps": "not a list"}), [])
        self.assertEqual(reg.see_wifi_scan(None), [])
        self.assertEqual(reg.see_wifi_scan("not a list"), [])


class LabelTests(unittest.TestCase):
    def test_a_known_ssid_is_the_label(self):
        reg = r.Registry()
        reg.see("aa:bb:cc:00:11:22", ssid="Home")
        self.assertEqual(reg.label("aa:bb:cc:00:11:22"), "Home")

    def test_an_unknown_or_hidden_bssid_falls_back_to_the_old_style(self):
        reg = r.Registry()
        self.assertEqual(reg.label("aa:bb:cc:00:11:22"), r.short_label("aa:bb:cc:00:11:22"))
        reg.see("aa:bb:cc:00:11:22", ssid="")           # seen, but hidden/unknown SSID
        self.assertEqual(reg.label("aa:bb:cc:00:11:22"), r.short_label("aa:bb:cc:00:11:22"))

    def test_whitespace_only_ssid_is_treated_as_unknown(self):
        reg = r.Registry()
        reg.see("aa:bb:cc:00:11:22", ssid="   ")
        self.assertEqual(reg.label("aa:bb:cc:00:11:22"), r.short_label("aa:bb:cc:00:11:22"))


class ChangedSecurityTests(unittest.TestCase):
    def test_a_real_change_is_flagged(self):
        reg = r.Registry()
        reg.see("aa:bb:cc:00:11:22", security="WPA2")
        self.assertTrue(reg.changed_security("aa:bb:cc:00:11:22", "Open"))
        self.assertFalse(reg.changed_security("aa:bb:cc:00:11:22", "WPA2"))

    def test_an_unknown_bssid_or_blank_security_is_never_a_change(self):
        reg = r.Registry()
        self.assertFalse(reg.changed_security("aa:bb:cc:00:11:22", "WPA2"))
        reg.see("aa:bb:cc:00:11:22")                    # seen, but no security reported yet
        self.assertFalse(reg.changed_security("aa:bb:cc:00:11:22", "WPA2"))
        reg.see("aa:bb:cc:00:11:22", security="WPA2")
        self.assertFalse(reg.changed_security("aa:bb:cc:00:11:22", ""))


class PersistenceTests(unittest.TestCase):
    def test_round_trip(self):
        reg = r.Registry()
        reg.see("aa:bb:cc:00:11:22", ssid="Home", channel=6, security="WPA2", rssi=-50, source="survey")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "bssids.json")
            reg.save(path)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            again = r.Registry()
            again.load(path)
            self.assertEqual(again.known("aa:bb:cc:00:11:22")["ssid"], "Home")

    def test_loading_a_missing_or_broken_file_never_raises(self):
        reg = r.Registry()
        reg.load("/nonexistent/path.json")
        self.assertEqual(reg.entries, {})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bad.json")
            open(path, "w").write("not json{{{")
            reg.load(path)
            self.assertEqual(reg.entries, {})

    def test_a_broken_entry_in_the_file_is_skipped_not_fatal(self):
        import json
        reg = r.Registry()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bssids.json")
            json.dump({"aa:bb:cc:00:11:22": {"ssid": "Home"}, "bad-key": {"ssid": "X"},
                      "aa:bb:cc:00:11:33": "not a dict"}, open(path, "w"))
            reg.load(path)
            self.assertEqual(list(reg.entries), ["aa:bb:cc:00:11:22"])

    def test_saving_trims_to_the_most_recently_seen(self):
        reg = r.Registry()
        for i in range(5):
            reg.see(f"aa:bb:cc:00:11:{i:02x}", now=float(i))
        import unittest.mock as mock
        with mock.patch.object(r, "MAX_ENTRIES", 2):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "bssids.json")
                reg.save(path)
                kept = r.Registry()
                kept.load(path)
                self.assertEqual(len(kept.entries), 2)
                self.assertIn("aa:bb:cc:00:11:04", kept.entries)   # the newest survives
                self.assertIn("aa:bb:cc:00:11:03", kept.entries)

    def test_a_bad_path_never_raises(self):
        r.Registry().save("/nonexistent/directory/that/cannot/be/made\x00/bssids.json")





class NotableTests(unittest.TestCase):
    """Alerts: a network never heard before, security that changes, a known name turning up with none."""
    AP = "aa:bb:cc:00:11:22"
    OTHER = "aa:bb:cc:00:11:33"

    def settled(self):
        from ops_gui import registry
        r = registry.Registry()
        r.see(self.AP, ssid="HomeNet", security="WPA2", source="survey", now=0.0)
        return r, registry.BASELINE_SECONDS + 100.0

    def test_nothing_is_new_until_the_registry_is_a_baseline(self):
        from ops_gui import registry
        r = registry.Registry()
        self.assertEqual(r.notable(self.AP, "HomeNet", "WPA2", "survey", now=5.0), [])
        r.see(self.AP, ssid="HomeNet", security="WPA2", source="survey", now=5.0)
        self.assertEqual(r.notable(self.OTHER, "Cafe", "WPA2", "survey", now=60.0), [])      # still the first session
        self.assertFalse(r.settled(60.0))
        self.assertTrue(r.settled(5.0 + registry.BASELINE_SECONDS))

    def test_a_never_heard_network_is_new_once_settled(self):
        r, now = self.settled()
        (event,) = r.notable(self.OTHER, "Cafe", "WPA2", "survey", now=now)
        self.assertEqual((event["kind"], event["bssid"], event["ssid"]), ("new", self.OTHER, "Cafe"))

    def test_one_heard_before_is_not_new(self):
        r, now = self.settled()
        self.assertEqual(r.notable(self.AP, "HomeNet", "WPA2", "survey", now=now), [])

    def test_changed_security_is_told_with_before_and_after(self):
        r, now = self.settled()
        (event,) = r.notable(self.AP, "HomeNet", "Open", "survey", now=now)
        self.assertEqual((event["kind"], event["old"], event["new"]), ("security", "WPA2", "Open"))

    def test_two_sources_wording_security_differently_is_not_a_change(self):
        r, now = self.settled()
        self.assertEqual(r.notable(self.AP, "HomeNet", "WPA2-PSK", "wifi", now=now), [])
        r.see(self.AP, ssid="HomeNet", security="WPA2-PSK", source="wifi", now=now)
        self.assertEqual(r.notable(self.AP, "HomeNet", "WPA2", "survey", now=now + 1), [])    # each against its own
        self.assertEqual(r.notable(self.AP, "HomeNet", "WPA2-PSK", "wifi", now=now + 1), [])

    def test_unknown_security_is_never_a_change(self):
        r, now = self.settled()
        for word in ("", None, "unknown"):
            self.assertEqual(r.notable(self.AP, "HomeNet", word, "survey", now=now), [])

    def test_a_known_name_with_no_security_on_a_new_address(self):
        r, now = self.settled()
        (event,) = r.notable(self.OTHER, "HomeNet", "Open", "survey", now=now)
        self.assertEqual((event["kind"], event["old"]), ("twin", "WPA2"))
        (plain,) = r.notable(self.OTHER, "HomeNet", "WPA2", "survey", now=now)          # same name, secured: just new
        self.assertEqual(plain["kind"], "new")
        (hidden,) = r.notable(self.OTHER, "", "Open", "survey", now=now)                # no name to compare
        self.assertEqual(hidden["kind"], "new")

    def test_observing_reports_then_records(self):
        r, now = self.settled()
        snap = {"t": now, "aps": [{"bssid": self.AP, "ssid": "HomeNet", "security": "Open", "channel": 6},
                                  {"bssid": self.OTHER, "ssid": "Cafe", "security": "WPA2", "channel": 1}, "junk"]}
        new, events = r.observe_survey(snap)
        self.assertEqual(new, [self.OTHER])
        self.assertEqual(sorted(e["kind"] for e in events), ["new", "security"])
        self.assertEqual(r.observe_survey(snap), ([], []))                              # told once, not every second
        new, events = r.observe_wifi_scan([{"bssid": "aa:bb:cc:00:11:44", "ssid": "Shop", "security": "WPA3"}], now=now)
        self.assertEqual((new, [e["kind"] for e in events]), (["aa:bb:cc:00:11:44"], ["new"]))
        self.assertEqual(r.observe_survey(None), ([], []))

    def test_the_wording_says_what_was_heard_and_no_more(self):
        from ops_gui import notify, registry
        cases = [({"kind": "security", "bssid": self.AP, "ssid": "HomeNet", "old": "WPA2", "new": "Open"}, "netsec", "warn"),
                 ({"kind": "twin", "bssid": self.OTHER, "ssid": "HomeNet", "old": "WPA2"}, "netsec", "warn"),
                 ({"kind": "new", "bssid": self.OTHER, "ssid": "", "security": ""}, "netnew", "info")]
        for event, source, level in cases:
            got_source, title, body, got_level = registry.describe(event)
            self.assertEqual((got_source, got_level), (source, level))
            self.assertIn(got_source, notify.DEFAULTS)
            self.assertIn(event["bssid"], body)
            for word in ("attack", "hacker", "evil", "malicious", "fake"):
                self.assertNotIn(word, (title + body).lower())
        self.assertIn("no name", registry.describe(cases[2][0])[2])

    def test_security_alerts_are_on_and_new_network_alerts_off_by_default(self):
        from ops_gui import notify
        self.assertTrue(notify.DEFAULTS["netsec"])
        self.assertFalse(notify.DEFAULTS["netnew"])
        self.assertIn("netsec", dict(notify.SOURCES))
        self.assertIn("netnew", dict(notify.SOURCES))


if __name__ == "__main__":
    unittest.main()
