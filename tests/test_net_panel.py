import json
import os
import tempfile
import unittest

from ops_gui import data, net_panel as n, pixel


class LinkTests(unittest.TestCase):
    def test_no_port(self):
        self.assertEqual(n.link_summary(None)[2], "off")

    def test_no_cable(self):
        head, detail, level = n.link_summary({"name": "eth0", "carrier": False, "hint": "Plug a cable into the port."})
        self.assertEqual((head, level), ("ETH0  NO LINK", "warn"))
        self.assertIn("Plug a cable", detail)

    def test_link_up_with_and_without_an_address(self):
        info = {"name": "eth0", "carrier": True, "speed_mbps": 1000, "addresses": ["192.168.1.5/24"]}
        self.assertEqual(n.link_summary(info), ("ETH0  1000 Mbit/s", "192.168.1.5/24", "good"))
        self.assertIn("DHCP", n.link_summary(dict(info, addresses=[]))[1])
        self.assertEqual(n.link_summary(dict(info, speed_mbps=None))[0], "ETH0  link up")

    def test_active_actions_need_a_link_and_an_address(self):
        self.assertFalse(n.can_probe(None))
        self.assertFalse(n.can_probe({"carrier": False, "addresses": ["10.0.0.2/24"]}))
        self.assertFalse(n.can_probe({"carrier": True, "addresses": []}))
        self.assertTrue(n.can_probe({"carrier": True, "addresses": ["10.0.0.2/24"]}))


class HostTests(unittest.TestCase):
    def test_merge_by_mac_across_sources(self):
        known = [{"mac": "AA:BB:CC:00:00:01", "ip": "192.168.0.7", "vendor": "Acme"}]
        heard = [{"mac": "aa:bb:cc:00:00:01", "ip": "", "name": "mybox"}, {"mac": "aa:bb:cc:00:00:02", "ip": "192.168.0.3"}]
        hosts = n.merge_hosts(("known", known), ("heard", heard))
        self.assertEqual([h["ip"] for h in hosts], ["192.168.0.3", "192.168.0.7"])        # sorted by address
        mine = next(h for h in hosts if h["mac"].endswith("01"))
        self.assertEqual((mine["name"], mine["vendor"], mine["via"]), ("mybox", "Acme", ["known", "heard"]))

    def test_odd_rows_are_skipped(self):
        self.assertEqual(n.merge_hosts(("known", [{"ip": "1.2.3.4"}, {}, None])), [])
        self.assertEqual(n.merge_hosts(("known", None)), [])

    def test_rows_for_the_table(self):
        hosts = n.merge_hosts(("known", [{"mac": "aa:bb:cc:00:00:01", "ip": "192.168.0.7"}]))
        self.assertEqual(n.host_table_rows(hosts), [("192.168.0.7", "-", "-", "aa:bb:cc:00:00:01", "known")])

    def test_unaddressed_hosts_sort_last(self):
        hosts = n.merge_hosts(("heard", [{"mac": "aa:00:00:00:00:01"}, {"mac": "aa:00:00:00:00:02", "ip": "10.0.0.9"}]))
        self.assertEqual(hosts[0]["ip"], "10.0.0.9")


class CommandTests(unittest.TestCase):
    def test_only_the_two_known_privileged_commands(self):
        self.assertEqual(n.LISTEN_ARGS[:4], ["sudo", "-n", "aiov2_ctl", "--net"])
        self.assertEqual(n.LISTEN_ARGS[-3:], ["--seconds", "30", "--json"])
        self.assertIn("--yes", n.SWEEP_ARGS)
        self.assertEqual(n.SWEEP_ARGS, ["sudo", "-n", "aiov2_ctl", "--net", "sweep", "--yes", "--json"])
        self.assertNotIn("sudo", n.ports_args("192.168.0.9"))

    def test_ports_command(self):
        self.assertEqual(n.ports_args("192.168.0.9"), ["aiov2_ctl", "--net", "ports", "192.168.0.9", "--yes", "--json"])

    def test_json_and_errors(self):
        self.assertEqual(n.parse_json_list(json.dumps({"interfaces": [{"name": "eth0"}]}), "interfaces"), [{"name": "eth0"}])
        self.assertEqual(n.parse_json_list("Error: No wired interface has a link."), [])
        self.assertEqual(n.parse_json_list(None), [])
        self.assertEqual(n.parse_json_list(json.dumps([{"mac": "a"}])), [{"mac": "a"}])
        self.assertEqual(n.error_line("x\nError: Listening needs root."), "Listening needs root.")
        self.assertEqual(n.error_line("fine"), "")

    def test_the_warnings_say_what_will_happen_and_to_whom(self):
        w = n.sweep_warning({"addresses": ["192.168.0.83/24"]})
        self.assertIn("192.168.0.83/24", w)
        self.assertIn("own subnet", w)
        self.assertIn("permission", w)
        p = n.ports_warning("192.168.0.9")
        self.assertIn("192.168.0.9", p)
        self.assertIn("permission", p)

    def test_nothing_aggressive_in_the_panel(self):
        import inspect
        code = inspect.getsource(n).split('"""')[2].lower()          # the code, not the docstring that says what it will not do
        for word in ("nmap", "hping", "deauth", "spoof", "brute", "exploit", "passwd", "credential"):
            self.assertNotIn(word, code)

    def test_the_sweep_command_is_exactly_the_one_sudo_allows(self):
        self.assertEqual(n.SWEEP_ARGS[3:], ["--net", "sweep", "--yes", "--json"])


class DiscoveryTests(unittest.TestCase):
    def make(self, d, name, type_="1", wireless=False, virtual=False, carrier="1", speed="1000"):
        real = os.path.join(d, "devices", "virtual" if virtual else "platform", name)
        os.makedirs(real, exist_ok=True)
        for k, v in (("type", type_), ("carrier", carrier), ("speed", speed)):
            with open(os.path.join(real, k), "w") as f:
                f.write(v)
        if wireless:
            os.makedirs(os.path.join(real, "wireless"))
        os.makedirs(os.path.join(d, "sys"), exist_ok=True)
        os.symlink(real, os.path.join(d, "sys", name))

    def test_tab_shows_only_with_a_real_wired_port(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d, "wlan0", wireless=True)
            self.make(d, "docker0", virtual=True)
            self.assertFalse(data.has_ethernet(os.path.join(d, "sys")))
            self.make(d, "eth0")
            self.assertTrue(data.has_ethernet(os.path.join(d, "sys")))
        self.assertFalse(data.has_ethernet("/nonexistent"))

    def test_the_tab_comes_after_wifi(self):
        self.assertEqual(data.tab_names({}, False, False, True, True)[:4], ["Home", "Overview", "Wi-Fi", "Network"])
        self.assertNotIn("Network", data.tab_names({}, False))

    def test_home_shows_a_plugged_in_cable_only(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d, "eth0", carrier="0")
            base = os.path.join(d, "sys")
            self.assertIsNone(pixel.ethernet_card(base))
            self.make(d, "eth1", carrier="1", speed="1000")
            card = pixel.ethernet_card(base, {"eth1": "192.168.1.5"})
            self.assertEqual((card["name"], card["state"], card["detail"], card["ip"]), ("eth1", "LINK", "1000 MBIT/S", "192.168.1.5"))
            self.assertIsNone(pixel.ethernet_card(os.path.join(d, "none")))


class WifiMatchTests(unittest.TestCase):
    """Hosts on the wired side matched, by address, with what Survey hears on the air."""
    HOSTS = [{"mac": "aa:bb:cc:00:00:01", "ip": "192.168.0.7", "name": "printer", "vendor": "Acme", "via": ["known"]},
             {"mac": "aa:bb:cc:00:00:02", "ip": "192.168.0.9", "name": "", "vendor": "", "via": ["heard"]}]
    AIR = {"aa:bb:cc:00:00:01": {"network": "HomeNet", "rssi": -61.4, "last": 1000.0}}

    def test_no_wifi_column_until_survey_has_heard_something(self):
        self.assertEqual(n.host_columns(None), ["Address", "Name", "Maker", "MAC", "Seen by"])
        self.assertEqual(n.host_columns({}), ["Address", "Name", "Maker", "MAC", "Seen by"])
        self.assertEqual(n.host_columns(self.AIR)[-1], "On Wi-Fi")
        self.assertEqual(len(n.host_table_rows(self.HOSTS)[0]), 5)
        self.assertEqual(len(n.host_table_rows(self.HOSTS, {}, 1000.0)[0]), 5)

    def test_a_host_heard_on_the_air_shows_its_network_and_signal(self):
        rows = n.host_table_rows(self.HOSTS, self.AIR, 1005.0)
        self.assertEqual(rows[0][-1], "HomeNet, -61 dBm")
        self.assertEqual(rows[1][-1], "-")                                 # not heard: says nothing, guesses nothing
        self.assertEqual(len(rows[0]), len(n.host_columns(self.AIR)))

    def test_matching_ignores_letter_case_and_forgets_after_a_while(self):
        self.assertEqual(n.wifi_note("AA:BB:CC:00:00:01", self.AIR, 1005.0), "HomeNet, -61 dBm")
        self.assertEqual(n.wifi_note("aa:bb:cc:00:00:01", self.AIR, 1000.0 + n.AIR_KEEP + 1), "-")

    def test_a_missing_signal_or_name_is_left_out_not_invented(self):
        air = {"aa:bb:cc:00:00:01": {"network": "", "rssi": None, "last": 1000.0}}
        self.assertEqual(n.wifi_note("aa:bb:cc:00:00:01", air, 1001.0), "a network")

    def test_hosts_are_handed_to_survey_by_address(self):
        self.assertEqual(n.hosts_by_mac(self.HOSTS)["aa:bb:cc:00:00:01"], {"ip": "192.168.0.7", "name": "printer"})

    def test_survey_names_a_device_it_hears_by_what_network_knows(self):
        from ops_gui import survey
        hosts = n.hosts_by_mac(self.HOSTS)
        self.assertEqual(survey.device_label("aa:bb:cc:00:00:01", hosts), "printer  (aa:bb:cc:00:00:01)")
        self.assertEqual(survey.device_label("AA:BB:CC:00:00:02", hosts), "192.168.0.9  (AA:BB:CC:00:00:02)")
        self.assertEqual(survey.device_label("02:11:22:33:44:55", hosts), "02:11:22:33:44:55")
        self.assertEqual(survey.device_label("02:11:22:33:44:55", None), "02:11:22:33:44:55")

    def test_survey_keeps_what_it_has_heard_for_the_network_page(self):
        from ops_gui import survey
        snap = {"t": 100.0, "aps": [{"bssid": "aa:bb:cc:00:11:22", "ssid": "HomeNet"}],
                "stations": [{"mac": "AA:BB:CC:00:00:01", "bssid": "aa:bb:cc:00:11:22", "rssi": -60.0, "last": 99.0}],
                "probes": [{"mac": "02:aa:aa:aa:aa:aa", "ssids": ["Other"], "rssi": -70.0}]}
        air = survey.air_devices(snap, {"aa:bb:cc:00:00:09": {"network": "Old", "rssi": -80, "last": 5.0}})
        self.assertEqual(air["aa:bb:cc:00:00:01"], {"network": "HomeNet", "rssi": -60.0, "last": 99.0})
        self.assertIn("aa:bb:cc:00:00:09", air)                            # earlier ones are kept
        self.assertNotIn("02:aa:aa:aa:aa:aa", air)                         # only probing: not on a network

    def test_a_probing_device_already_listed_as_a_station_is_not_listed_twice(self):
        from ops_gui import survey
        snap = {"t": 100.0, "aps": [{"bssid": "aa:bb:cc:00:11:22", "ssid": "HomeNet"}],
                "stations": [{"mac": "aa:bb:cc:00:00:01", "bssid": "aa:bb:cc:00:11:22", "rssi": -60.0, "last": 99.0}],
                "probes": [{"mac": "aa:bb:cc:00:00:01", "ssids": ["HomeNet"], "rssi": -60.0}]}
        rows = survey.station_rows(snap, hosts=n.hosts_by_mac(self.HOSTS))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mac"], "printer  (aa:bb:cc:00:00:01)")


if __name__ == "__main__":
    unittest.main()
