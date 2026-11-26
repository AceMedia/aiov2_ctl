import json
import unittest
from unittest import mock

from ops_gui import data

POWER = {"source": "AC", "status": "Charging", "capacity": 100, "direction": "idle",
         "mode": "AC powering system", "voltage": 4.21, "current": 0.03, "power": 0.12}


class FetchTests(unittest.TestCase):
    def setUp(self):                        # these are about asking aiov2_ctl: no shared reading to hand
        patcher = mock.patch.object(data, "shared_snapshot", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reads_json(self):
        out = json.dumps({"rails": {"GPS": "on"}})
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout=out)):
            self.assertEqual(data.fetch_snapshot()["rails"]["GPS"], "on")

    def test_every_failure_gives_empty(self):
        for stdout in ("", "not json", "[]", "{}"):
            with mock.patch("subprocess.run", return_value=mock.Mock(stdout=stdout)):
                self.assertEqual(data.fetch_snapshot(), {})
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            self.assertEqual(data.fetch_snapshot(), {})


class BatteryCardTests(unittest.TestCase):
    def test_normal(self):
        big, lines, level = data.battery_card({"power": POWER, "gauge_ignored": {"ignored": 0, "taken": 8}})
        self.assertEqual((big, level), ("100%", "ok"))
        self.assertIn("4.21 V", lines[0])

    def test_gauge_warning_and_ignored_readings(self):
        big, lines, level = data.battery_card({"power": POWER, "gauge_warning": True,
                                               "gauge_ignored": {"ignored": 3, "taken": 8}})
        self.assertEqual(level, "warn")
        self.assertIn("3 of 8", " ".join(lines))

    def test_no_reading_says_why(self):
        big, lines, level = data.battery_card({"power": None, "gauge_ignored": {"ignored": 8, "taken": 8}})
        self.assertEqual((big, level), ("n/a", "none"))
        self.assertIn("contacts", lines[0])
        self.assertIn("No battery", data.battery_card({"power": None})[1][0])

    def test_empty_snapshot(self):
        self.assertEqual(data.battery_card({})[0], "n/a")


class OtherCardTests(unittest.TestCase):
    def test_rail_order(self):
        pills = data.rail_pills({"rails": {"USB": "on", "GPS": "off", "SDR": "on", "LORA": "off"}})
        self.assertEqual([n for n, _ in pills], ["GPS", "LORA", "SDR", "USB"])

    def test_companions_hidden_when_none(self):
        self.assertEqual(data.companion_lines({"companions": []}), [])
        self.assertEqual(data.companion_lines({}), [])

    def test_companion_line(self):
        out = data.companion_lines({"companions": [{"label": "Flipper Zero", "tty": "/dev/ttyACM1"}]})
        self.assertEqual(out, ["Flipper Zero  /dev/ttyACM1"])




WIFI = {"radios": [{"name": "wlan1", "kind": "internal-usb", "driver": "mt7921u", "up": True},
                   {"name": "wlan0", "kind": "internal", "driver": "brcm", "up": False}],
        "networks": [
            {"ssid": "", "bssid": "a", "channel": 6, "freq_mhz": 2437, "signal_dbm": -80, "security": "WPA2", "radio": "wlan1"},
            {"ssid": "Home", "bssid": "b", "channel": 1, "freq_mhz": 2412, "signal_dbm": -50, "security": "WPA3", "radio": "wlan1"},
            {"ssid": "Cafe", "bssid": "c", "channel": 6, "freq_mhz": 2437, "signal_dbm": -70, "security": "", "radio": "wlan0"},
            {"ssid": "Fast", "bssid": "d", "channel": 36, "freq_mhz": 5180, "signal_dbm": -60, "security": "WPA2", "radio": "wlan1"}]}


class WifiTests(unittest.TestCase):
    def test_rows_sorted_and_tagged(self):
        rows = data.wifi_rows(WIFI)
        self.assertEqual([r[0] for r in rows], ["Home", "Fast", "Cafe", "(hidden)"])
        self.assertEqual(rows[0][5], "wlan1")
        self.assertEqual(rows[2][4], "open")

    def test_empty(self):
        self.assertEqual(data.wifi_rows({}), [])
        self.assertEqual(data.congestion_summary({}), ["No networks seen."])

    def test_bars(self):
        self.assertEqual(data.signal_bars(-50), "####")
        self.assertEqual(data.signal_bars(-90), "----")
        self.assertEqual(data.signal_bars(None), "----")

    def test_congestion(self):
        c = data.channel_congestion(WIFI)
        self.assertEqual(c["2.4"], {6: 2, 1: 1})
        self.assertEqual(c["5"], {36: 1})

    def test_summary_suggests_quiet_channel(self):
        text = "\n".join(data.congestion_summary(WIFI))
        self.assertIn("busiest channel 6 (2)", text)
        self.assertIn("channel 11", text)

    def test_radios(self):
        self.assertEqual(data.radio_lines(WIFI)[0], "wlan1 internal-usb mt7921u up")

    def test_scan_fetch_failure(self):
        with mock.patch("subprocess.run", side_effect=OSError()):
            self.assertEqual(data.fetch_wifi_scan(), {})


class OverviewCardTests(unittest.TestCase):
    def test_radios(self):
        r = {"radios": [{"name": "wlan1", "kind": "internal-usb", "up": True},
                        {"name": "flipper-marauder", "kind": "flipper-marauder", "up": False}]}
        self.assertEqual(data.radios_overview(r), ["wlan1  internal usb  up",
                                                   "flipper-marauder  flipper marauder  down"])
        self.assertEqual(data.radios_overview({}), [])

    def test_system_lines_never_raise(self):
        with mock.patch.object(data, "_read", return_value=""), \
                mock.patch.object(data.os, "statvfs", side_effect=OSError):
            self.assertEqual(data.system_lines(), [])
        lines = data.system_lines()
        self.assertIsInstance(lines, list)


class FlipperTests(unittest.TestCase):
    ST = {"mode": "usb", "device": "Flipper Zero", "battery": {"percent": 80}, "sd_card": "ok",
          "apps": ["a", "b"], "wifi_board": None, "suggestions": ["Update firmware"]}

    def test_detected(self):
        self.assertTrue(data.flipper_detected(self.ST))
        for s in ({}, None, {"device": ""}, {"mode": "none"}):
            self.assertFalse(data.flipper_detected(s))
            self.assertEqual(data.flipper_lines(s), [])

    def test_bridge_mode_counts_as_detected(self):
        s = {"mode": "bridge", "device": {}, "port": "/dev/ttyACM1", "suggestions": ["Press Back"]}
        self.assertTrue(data.flipper_detected(s))
        self.assertIn("Tip: Press Back", data.flipper_lines(s))

    def test_lines(self):
        lines = data.flipper_lines(self.ST)
        self.assertIn("Device: Flipper Zero", lines)
        self.assertIn("Battery: percent 80", lines)
        self.assertIn("Apps: a, b", lines)
        self.assertIn("Tip: Update firmware", lines)
        self.assertFalse(any("Wi-Fi board" in l for l in lines))

    def test_the_real_device_dictionary_is_summarised(self):
        s = {"mode": "cli", "port": "/dev/ttyACM1",
             "device": {"hardware_model": "Flipper Zero", "hardware_name": "In90roth", "hardware_uid": "C0D9",
                        "firmware_version": "unlshd-095", "firmware_origin_fork": "Unleashed",
                        "radio_fus_sram2b": "16K"},
             "battery": {"charge.level": "87", "charge.state": "discharging"},
             "apps": [f"app{i}" for i in range(10)], "sd_card": "ok"}
        lines = data.flipper_lines(s)
        self.assertIn("Device: Flipper Zero (In90roth)", lines)
        self.assertIn("Firmware: unlshd-095 (Unleashed)", lines)
        self.assertIn("Battery: 87% (discharging)", lines)
        self.assertIn("Apps: app0, app1, app2, app3, app4, app5 and 4 more", lines)
        self.assertFalse(any("hardware_uid" in l or "radio_fus" in l for l in lines))
        self.assertLess(max(len(l) for l in lines), 80)

    def test_facts_are_a_tidy_table(self):
        s = {"mode": "cli", "port": "/dev/ttyACM1", "sd_card": "present", "apps": ["a", "b"],
             "device": {"hardware_model": "Flipper Zero", "hardware_name": "In90roth",
                        "firmware_version": "unlshd-095", "firmware_origin_fork": "Unleashed"},
             "battery": {"charge.level": "100", "charge.state": "charged"},
             "suggestions": ["Try <this>"]}
        facts = dict(data.flipper_facts(s))
        self.assertEqual(facts["Device"], "Flipper Zero (In90roth)")
        self.assertEqual(facts["Mode"], "CLI")
        self.assertEqual(facts["Battery"], "100% (charged)")
        self.assertNotIn("Apps", facts)
        self.assertEqual(dict(data.flipper_facts(s, with_apps=True))["Apps"], "a, b")
        self.assertEqual(data.flipper_facts({}), [])
        html_text = data.facts_html(data.flipper_facts(s), data.flipper_tips(s))
        self.assertIn("<table", html_text)
        self.assertIn("Try &lt;this&gt;", html_text)
        self.assertEqual(data.facts_html([]), "")
        bridge = {"mode": "bridge", "device": {}, "port": "/dev/ttyACM1"}
        self.assertEqual(dict(data.flipper_facts(bridge))["Mode"], "USB-UART bridge")

    def test_png_removes_stale_and_checks_result(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "screen.png")
            open(p, "w").close()

            def fake(cmd, **kw):
                self.assertFalse(os.path.exists(cmd[-1]))
                with open(cmd[-1], "wb") as f:
                    f.write(b"png")
            with mock.patch("subprocess.run", side_effect=fake):
                self.assertEqual(data.fetch_flipper_png(d), p)
            with mock.patch("subprocess.run", side_effect=OSError()):
                self.assertIsNone(data.fetch_flipper_png(d))


class TabTests(unittest.TestCase):
    def test_keys_and_footer(self):
        self.assertEqual(data.tab_keys(["A", "B"]), {"1": "A", "2": "B"})
        self.assertIn("s: scan", data.footer_for("Wi-Fi", 4))
        self.assertIn("ON", data.footer_for("Flipper", 4, live=True))
        self.assertNotIn("scan", data.footer_for("Overview", 3))

    def test_companions_text(self):
        self.assertEqual(data.companions_text({"companions": [{"label": "Flipper", "tty": "/dev/ttyACM0"}]}),
                         ["Flipper  /dev/ttyACM0"])
        self.assertEqual(data.companions_text({}, {}), ["No companion devices detected."])
        self.assertEqual(data.companions_text({}, {"companions": [{"name": "x"}]}), ["x"])

class AntennaTabTests(unittest.TestCase):
    def snap(self, **rails):
        base = {"GPS": "off", "LORA": "off", "SDR": "off", "USB": "off"}
        base.update(rails)
        return {"rails": base}

    def test_only_powered_antennas_get_tabs(self):
        self.assertEqual(data.tab_names(self.snap(), False), ["Overview", "Wi-Fi", "Settings", "Help"])
        self.assertEqual(data.tab_names(self.snap(GPS="on", SDR="on"), False),
                         ["Overview", "Wi-Fi", "GPS", "SDR", "Settings", "Help"])
        self.assertEqual(data.tab_names(self.snap(LORA="on"), True),
                         ["Overview", "Wi-Fi", "LoRa", "Flipper", "Settings", "Help"])

    def test_no_snapshot_is_safe(self):
        self.assertEqual(data.tab_names({}, False), ["Overview", "Wi-Fi", "Settings", "Help"])
        self.assertEqual(data.tab_names(None, False), ["Overview", "Wi-Fi", "Settings", "Help"])

    def test_antenna_lines_name_the_rail(self):
        for name, rail in data.ANTENNA_RAILS:
            lines = data.antenna_lines(name, self.snap(**{rail: "on"}))
            self.assertIn(f"Power rail {rail}: on", lines[0])


class ThemeTests(unittest.TestCase):
    def test_saved_choice_wins_otherwise_system(self):
        self.assertEqual(data.resolve_theme("light", True), "light")
        self.assertEqual(data.resolve_theme("dark", False), "dark")
        self.assertEqual(data.resolve_theme(None, True), "dark")
        self.assertEqual(data.resolve_theme("bogus", False), "light")

    def test_settings_round_trip_and_privacy(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "settings.json")
            self.assertEqual(data.load_settings(path), {})
            self.assertTrue(data.save_settings({"theme": "dark"}, path))
            self.assertEqual(data.load_settings(path), {"theme": "dark"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_corrupt_settings_are_ignored(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            with open(path, "w") as f:
                f.write("{not json")
            self.assertEqual(data.load_settings(path), {})


class PixelIconTests(unittest.TestCase):
    def test_theme_bitmaps_are_16_by_16(self):
        for bmp in (data.SUN, data.MOON):
            self.assertEqual(len(bmp), 16)
            self.assertTrue(all(len(r) == 16 and set(r) <= {".", "#"} for r in bmp))

    def test_sun_is_symmetric(self):
        self.assertEqual(data.SUN, data.SUN[::-1])
        self.assertTrue(all(r == r[::-1] for r in data.SUN))

    def test_every_tab_has_its_icon_files(self):
        for tab in data.TAB_ICONS:
            for size in (32, 64):
                self.assertTrue(data.tab_icon_path(tab, size), f"{tab} {size}")
        self.assertIsNone(data.tab_icon_path("Nope"))


class RailControlTests(unittest.TestCase):
    MEASURE = ("Measure: GPS (GPIO27)\nPower source: AC online\nCurrent state: OFF\n\nResults\n"
               "----------------\nOFF: 3.40 W\nON : 3.75 W\n\u0394   +0.35 W\n")

    def test_set_rail_runs_the_cli_and_reports(self):
        calls = []

        def runner(args, timeout):
            calls.append(args)
            return True, ""
        ok, msg = data.set_rail("LORA", True, runner)
        self.assertTrue(ok)
        self.assertEqual(calls, [["LORA", "on"]])
        self.assertIn("LoRa rail turned on", msg)

    def test_set_rail_refuses_unknown_names(self):
        ok, _ = data.set_rail("WIFI", True, lambda a, t: (True, ""))
        self.assertFalse(ok)

    def test_failure_message_carries_the_reason(self):
        ok, msg = data.set_rail("GPS", False, lambda a, t: (False, "sudo: a password is required"))
        self.assertFalse(ok)
        self.assertIn("password", msg)

    def test_parse_measure(self):
        m = data.parse_measure(self.MEASURE)
        self.assertEqual((m["off"], m["on"], m["delta"], m["source"]), (3.40, 3.75, 0.35, "AC"))
        self.assertIsNone(data.parse_measure("Sampling failed."))

    def test_measure_rail_uses_the_cli_measure(self):
        calls = []

        def runner(args, timeout):
            calls.append(args)
            return True, self.MEASURE
        self.assertEqual(data.measure_rail("SDR", runner)["delta"], 0.35)
        self.assertEqual(calls[0][:2], ["--measure", "SDR"])
        self.assertIsNone(data.measure_rail("NOPE", runner))

    def test_power_text_and_estimate(self):
        cache = {"GPS": {"delta": 0.35, "when": "24 Nov 09:00", "source": "battery"}, "SDR": {"delta": 0.5},
                 "LORA": {"delta": -0.02, "source": "AC"}}
        self.assertIn("+0.35 W", data.rail_power_text("GPS", cache))
        self.assertIn("unplug", data.rail_power_text("LORA", cache))
        self.assertEqual(data.rail_power_text("USB", cache), "not measured")
        snap = {"rails": {"GPS": "on", "LORA": "off", "SDR": "on", "USB": "off"}}
        self.assertEqual(data.rails_estimate(snap, cache), 0.85)
        self.assertIsNone(data.rails_estimate({"rails": {"GPS": "off"}}, cache))

    def test_warnings(self):
        radios = {"radios": [{"name": "wlan1", "kind": "internal-usb"}]}
        w = data.rail_warning("USB", False, {}, radios, True)
        self.assertIn("wlan1", w)
        self.assertIn("Flipper", w)
        self.assertEqual(data.rail_warning("USB", True, {}, radios, True), "")
        self.assertEqual(data.rail_warning("GPS", False, {}), "")
        self.assertIn("meshtasticd", data.measure_warning("LORA"))

    def test_cache_round_trip(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c", "rail-power.json")
            data.save_rail_power("GPS", {"delta": 0.3, "source": "AC"}, "24 Nov", path)
            self.assertEqual(data.load_rail_power(path)["GPS"]["delta"], 0.3)


class RailCardTests(unittest.TestCase):
    def test_every_rail_has_a_label_blurb_and_icon(self):
        for name in data.RAILS:
            label, icon, blurb = data.RAIL_INFO[name]
            self.assertTrue(label and blurb)
            self.assertTrue(data.rail_icon_path(name, 32), name)

    def test_measure_only_makes_sense_on_battery(self):
        self.assertFalse(data.on_battery({"power": {"source": "AC"}}))
        self.assertTrue(data.on_battery({"power": {"source": "battery"}}))
        self.assertFalse(data.on_battery({}))


class ResponsiveTests(unittest.TestCase):
    def test_columns_grow_with_width(self):
        self.assertEqual([data.rail_columns(w) for w in (600, 819, 820, 1280)], [1, 1, 2, 2])
        self.assertEqual([data.lower_columns(w) for w in (700, 900, 1150, 1239, 1280)], [1, 2, 3, 3, 4])

    def test_stat_tiles(self):
        snap = {"power": {"voltage": 4.2, "current": 0.1, "power": 0.42}}
        self.assertEqual(data.stat_tiles(snap), [("Voltage", "4.20 V"), ("Current", "+0.10 A"), ("Power", "+0.42 W")])
        self.assertEqual(data.stat_tiles({}), [])


class FlipperUnpluggedTests(unittest.TestCase):
    """The Flipper tab goes when the Flipper is unplugged, but not just because one probe got no answer."""

    def test_a_flipper_on_usb_is_seen_by_its_vendor_and_product(self):
        self.assertTrue(data.flipper_plugged([("1d6b", "0002"), ("0483", "5740")]))
        self.assertFalse(data.flipper_plugged([("1d6b", "0002"), ("0e8d", "7961")]))
        self.assertFalse(data.flipper_plugged([]))

    def test_no_answer_and_nothing_on_usb_means_unplugged(self):
        self.assertEqual(data.flipper_report({}, False), {"absent": True})
        self.assertEqual(data.flipper_report(None, False), {"absent": True})

    def test_no_answer_while_still_plugged_in_keeps_what_was_showing(self):
        self.assertEqual(data.flipper_report({}, True), {})

    def test_an_answer_is_passed_on_as_it_is(self):
        st = {"mode": "usb", "device": "Flipper Zero"}
        self.assertIs(data.flipper_report(st, True), st)
        self.assertIs(data.flipper_report(st, False), st)

    def test_the_absent_report_is_a_fresh_copy_each_time(self):
        self.assertIsNot(data.flipper_report({}, False), data.FLIPPER_ABSENT)

    def test_usb_ids_reads_sysfs_and_shrugs_off_a_missing_folder(self):
        import os
        import tempfile
        self.assertEqual(data.usb_ids("/nonexistent/usb"), [])
        with tempfile.TemporaryDirectory() as root:
            for name, vendor, product in (("1-1", "0483", "5740"), ("1-2", "0E8D", "7961")):
                os.mkdir(os.path.join(root, name))
                for leaf, value in (("idVendor", vendor), ("idProduct", product)):
                    with open(os.path.join(root, name, leaf), "w") as f:
                        f.write(value + "\n")
            os.mkdir(os.path.join(root, "1-0:1.0"))                       # an interface: no ids of its own
            self.assertEqual(sorted(data.usb_ids(root)), [("0483", "5740"), ("0e8d", "7961")])


class RailsMarkTests(unittest.TestCase):
    """Where the mark left by a rail switch lives. aiov2_ctl uses the same rule; if one changes, both must."""

    def test_it_is_in_the_users_runtime_folder_when_there_is_one(self):
        path = data.rails_stamp_path({}, 1000, isdir=lambda p: True)
        self.assertEqual(path, "/run/user/1000/aiov2_ctl.rails")

    def test_without_a_runtime_folder_it_falls_back_to_tmp(self):
        self.assertEqual(data.rails_stamp_path({}, 1000, isdir=lambda p: False), "/tmp/aiov2_ctl-1000.rails")

    def test_under_sudo_it_is_still_the_calling_users(self):
        self.assertEqual(data.rails_stamp_path({"SUDO_UID": "1000"}, None, isdir=lambda p: True),
                         "/run/user/1000/aiov2_ctl.rails")


class SharedStatusTests(unittest.TestCase):
    """The app uses a reading of the board someone else just took, when it still can."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        import os
        self.path = os.path.join(self.dir.name, "x.status.json")
        self.mark = os.path.join(self.dir.name, "x.rails")

    def keep(self, written, status):
        with open(self.path, "w") as f:
            json.dump({"written": written, "status": status}, f)

    def test_a_recent_reading_is_used(self):
        self.keep(100.0, {"rails": {"GPS": "on"}})
        self.assertEqual(data.shared_snapshot(2.0, 101.0, self.path, self.mark)["rails"]["GPS"], "on")

    def test_an_old_one_is_not(self):
        self.keep(100.0, {"rails": {"GPS": "on"}})
        self.assertIsNone(data.shared_snapshot(2.0, 103.0, self.path, self.mark))

    def test_a_rail_switched_since_outdates_it(self):
        import os
        self.keep(100.0, {"rails": {"GPS": "on"}})
        open(self.mark, "w").close()
        os.utime(self.mark, (100.5, 100.5))
        self.assertIsNone(data.shared_snapshot(2.0, 100.6, self.path, self.mark))

    def test_no_file_or_a_damaged_one_means_ask_aiov2_ctl(self):
        self.assertIsNone(data.shared_snapshot(2.0, 100.0, self.path, self.mark))
        for text in ("nope", "[]", json.dumps({"written": 100.0, "status": {"x": 1}})):
            with open(self.path, "w") as f:
                f.write(text)
            self.assertIsNone(data.shared_snapshot(2.0, 100.1, self.path, self.mark))

    def test_the_rule_is_the_one_aiov2_ctl_uses(self):
        self.assertTrue(data.status_cache_fresh(100.0, 101.5, 99.0, 2.0))
        self.assertFalse(data.status_cache_fresh(100.0, 100.2, 100.1, 2.0))
        self.assertFalse(data.status_cache_fresh(100.0, 99.0, None, 2.0))
        self.assertFalse(data.status_cache_fresh(None, 100.0, None, 2.0))
        self.assertEqual(data.status_cache_path({}, 1000, isdir=lambda p: True), "/run/user/1000/aiov2_ctl.status.json")

    def test_fetching_uses_the_shared_reading_and_does_not_start_aiov2_ctl(self):
        with mock.patch.object(data, "shared_snapshot", return_value={"rails": {"GPS": "off"}}), \
                mock.patch("subprocess.run", side_effect=AssertionError("should not be started")):
            self.assertEqual(data.fetch_snapshot(), {"rails": {"GPS": "off"}})

    def test_companions_come_from_the_status_without_asking_again(self):
        snap = {"rails": {"USB": "on"}, "companions": [{"label": "Flipper Zero"}]}
        with mock.patch("subprocess.run", side_effect=AssertionError("should not be started")):
            self.assertEqual(data.fetch_companions(snap),
                             {"usb_rail": "on", "devices": [{"label": "Flipper Zero"}]})

    def test_a_status_without_companions_still_asks(self):
        out = json.dumps({"usb_rail": "off", "devices": []})
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout=out)):
            self.assertEqual(data.fetch_companions({"rails": {}}), {"usb_rail": "off", "devices": []})
            self.assertEqual(data.fetch_companions(), {"usb_rail": "off", "devices": []})


class StuckGaugeTests(unittest.TestCase):
    """A percentage that is an estimate is shown as one."""
    EST = {"power": dict(POWER, capacity=52, capacity_source="voltage", capacity_gauge=100, energy_full_wh=24.79),
           "gauge_warning": True}
    REAL = {"power": dict(POWER, capacity=52, capacity_source="gauge", capacity_gauge=52)}

    def test_an_estimate_is_marked_and_explained(self):
        head, lines, level = data.battery_card(self.EST)
        self.assertEqual(head, "~52%")
        self.assertEqual(level, "warn")
        self.assertTrue(any("says 100%" in line and "rough" in line for line in lines))

    def test_a_real_reading_is_shown_plainly(self):
        head, lines, level = data.battery_card(self.REAL)
        self.assertEqual((head, level), ("52%", "ok"))
        self.assertFalse(data.battery_estimated(self.REAL))
        self.assertFalse(data.battery_estimated({"power": POWER}))          # an older aiov2_ctl says nothing
        self.assertFalse(data.battery_estimated(None))

    def test_the_percentage_text(self):
        self.assertEqual(data.percent_text(52), "52%")
        self.assertEqual(data.percent_text(52, True), "~52%")
        self.assertEqual(data.percent_text(None, True), "---")

    def test_the_pack_size_is_used_only_when_it_is_believable(self):
        self.assertAlmostEqual(data.known_wh_per_percent(self.EST), 0.2479)
        self.assertIsNone(data.known_wh_per_percent(self.REAL))
        self.assertIsNone(data.known_wh_per_percent({"power": {"energy_full_wh": 0}}))
        self.assertIsNone(data.known_wh_per_percent({"power": {"energy_full_wh": 5000}}))


class ChargingWordTests(unittest.TestCase):
    def test_discharging_is_not_charging(self):
        self.assertFalse(data.is_charging({"status": "Discharging", "direction": "discharging"}))
        self.assertFalse(data.is_charging({"status": "Discharging"}))
        self.assertFalse(data.is_charging({"status": "Not charging"}))
        self.assertFalse(data.is_charging(None))

    def test_charging_is(self):
        self.assertTrue(data.is_charging({"status": "Charging", "direction": "charging"}))
        self.assertTrue(data.is_charging({"status": "Charging"}))

    def test_a_full_battery_on_the_charger_that_takes_no_current_is_not_charging(self):
        self.assertFalse(data.is_charging({"status": "Charging", "direction": "idle"}))

    def test_an_estimated_percentage_leaves_room_for_its_mark(self):
        from ops_gui import pixel
        self.assertEqual(pixel.power_chars(400, 45, True), pixel.power_chars(400, 100))   # "~45%" is as wide as "100%"
        self.assertLess(pixel.power_chars(400, 45, True), pixel.power_chars(400, 45))


class FlipperFrameTests(unittest.TestCase):
    """The Flipper's 128 by 64 screen is shown whole, inside its 384 by 192 box."""

    def test_a_frame_larger_than_the_box_is_shrunk_to_fit_whole(self):
        self.assertEqual(data.fit_frame(1024, 512, 384, 192), (384, 192))
        self.assertEqual(data.fit_frame(512, 256, 384, 192), (384, 192))

    def test_a_small_frame_is_enlarged_by_a_whole_number(self):
        self.assertEqual(data.fit_frame(128, 64, 384, 192), (384, 192))
        self.assertEqual(data.fit_frame(128, 64, 400, 250), (384, 192))        # x3, not x3.125

    def test_the_shape_is_kept_in_a_box_of_another_shape(self):
        w, h = data.fit_frame(1024, 512, 300, 300)
        self.assertEqual((w, h), (300, 150))

    def test_it_never_comes_out_larger_than_the_box(self):
        for frame in ((128, 64), (1024, 512), (640, 320), (130, 70)):
            for box in ((384, 192), (200, 300), (97, 51)):
                w, h = data.fit_frame(*frame, *box)
                self.assertLessEqual(w, box[0])
                self.assertLessEqual(h, box[1])

    def test_nothing_to_draw_gives_no_size(self):
        self.assertEqual(data.fit_frame(0, 64, 384, 192), (0, 0))
        self.assertEqual(data.fit_frame(128, 64, 0, 192), (0, 0))


if __name__ == "__main__":
    unittest.main()
