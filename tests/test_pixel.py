import time
import unittest

from ops_gui import pixel as px


class FontTests(unittest.TestCase):
    def test_nearest_size_and_family(self):
        self.assertEqual([px.nearest_size(v) for v in (5, 16, 20, 70)], [12, 16, 16, 64])
        have = {"Spleen 8x16", "Terminus", "DejaVu Sans"}
        self.assertEqual(px.font_family(16, have), "Spleen 8x16")
        self.assertEqual(px.font_family(32, have), "Terminus")
        self.assertIsNone(px.font_family(16, {"DejaVu Sans"}))

    def test_palettes(self):
        self.assertEqual(set(px.PALETTES["dark"]), set(px.PALETTES["light"]))
        self.assertEqual(px.palette("dark")["accent"], "#d70a53")
        self.assertIs(px.palette("anything"), px.PALETTES["dark"])

    def test_text_roles_are_readable_sizes(self):
        self.assertGreaterEqual(min(px.SPLEEN), 12)
        self.assertEqual(px.nearest_size(24), 24)


class LevelTests(unittest.TestCase):
    def test_basic_levels(self):
        self.assertEqual(px.level_for(None, 1, 2), "dim")
        self.assertEqual(px.level_for(5, 70, 90), "good")
        self.assertEqual(px.level_for(75, 70, 90), "warn")
        self.assertEqual(px.level_for(95, 70, 90), "hot")

    def test_hysteresis_holds_a_level_until_clearly_below(self):
        self.assertEqual(px.level_for(68, 70, 90, "warn"), "warn")     # just under the line: hold
        self.assertEqual(px.level_for(64, 70, 90, "warn"), "good")     # clearly below: release
        self.assertEqual(px.level_for(72, 70, 90, "good"), "warn")     # rising changes at once
        self.assertEqual(px.level_for(88, 70, 90, "hot"), "hot")
        self.assertEqual(px.level_for(80, 70, 90, "hot"), "warn")

    def test_segments_clock_and_numbers(self):
        self.assertEqual([px.lit_segments(p, 20) for p in (0, 1, 5, 6, 100, None)], [0, 1, 1, 2, 20, 0])
        t = time.mktime((2026, 11, 24, 14, 7, 0, 0, 0, -1))
        self.assertEqual((px.clock_text(t), px.clock_text(t, False), px.date_text(t)), ("14:07", "14 07", "TUE 24 NOV"))
        self.assertEqual((px.pct_text(7), px.pct_text(100), px.pct_text(None)), ("  7%", "100%", "  -"))
        self.assertEqual(px.temp_text(51.4), " 51C")
        self.assertEqual((px.uptime_short(90), px.uptime_short(3725), px.uptime_short(90000)), ("1m", "1h02", "1d1h"))
        self.assertEqual(px.average([None, 10, 20, 30, 40]), 30)
        self.assertIsNone(px.average([None]))
        self.assertEqual(px.fit("abcdefghij", 5), "abcd~")
        self.assertEqual(px.fit("abc", 5), "abc")


class LayoutTests(unittest.TestCase):
    def test_everything_fits_without_overlap(self):
        for w, h in ((1256, 560), (1280, 664), (1100, 520), (900, 500)):
            lay = px.layout(w, h)
            rects = px.all_rects(lay) + lay["rack"] + lay["vitals"]
            for x, y, rw, rh in rects:
                self.assertTrue(x >= 0 and y >= 0 and x + rw <= w and y + rh <= h and rw > 0 and rh > 0, (w, h, (x, y, rw, rh)))
            top = [lay["clock"], lay["power"], lay["ticker"]]
            self.assertTrue(all(a[0] + a[2] <= b[0] for a, b in zip(top, top[1:])))
            self.assertLessEqual(lay["clock"][1] + lay["clock"][3], lay["rack_panel"][1])
            self.assertLessEqual(lay["rack_panel"][1] + lay["rack_panel"][3], lay["keys"][0][1])
            self.assertEqual((len(lay["rack"]), len(lay["vitals"]), len(lay["links"]), len(lay["keys"])), (6, 4, 3, 6))

    def test_rows_are_tall_enough_for_24_pixel_text(self):
        lay = px.layout(1256, 560)
        self.assertGreaterEqual(lay["rack"][0][3], 44)
        self.assertGreaterEqual(lay["vitals"][0][3], 70)
        self.assertGreaterEqual(lay["links"][0][3], 100)
        self.assertGreaterEqual(lay["clock"][3], 100)

    def test_rows_stack_inside_their_panels(self):
        lay = px.layout(1256, 560)
        px_, py, pw, ph = lay["rack_panel"]
        self.assertLessEqual(lay["rack"][-1][1] + lay["rack"][-1][3], py + ph)
        vx, vy, vw, vh = lay["vitals_panel"]
        self.assertLessEqual(lay["vitals"][-1][1] + lay["vitals"][-1][3], vy + vh)

    def test_key_hit_test(self):
        keys = px.layout(1256, 560)["keys"]
        self.assertEqual(px.key_at(keys[2][0] + 3, keys[2][1] + 3, keys), 2)
        self.assertIsNone(px.key_at(0, 0, keys))


class DataTests(unittest.TestCase):
    def test_vitals(self):
        rows = px.vital_rows({"cpu": 12.0, "temp": 52.0, "mem": (2.5, 15.8, 16.0), "disk": 20.0})
        self.assertEqual([r[0] for r in rows], ["CPU", "TEMP", "RAM", "DISK"])
        self.assertEqual(rows[0][1:], (" 12%", 12.0, "good"))
        self.assertEqual(rows[2][1], "2.5G")
        self.assertEqual(px.vital_rows({})[0][1:], ("  -", None, "dim"))
        warm = px.vital_rows({"cpu": 72.0}, rows)
        self.assertEqual(warm[0][3], "warn")
        self.assertEqual(px.vital_rows({"cpu": 68.0}, warm)[0][3], "warn")      # held by the hysteresis

    def test_parsing(self):
        ip = "1: lo    inet 127.0.0.1/8 scope host lo\n3: wlan0    inet 192.168.0.83/24 brd x scope global wlan0\n"
        self.assertEqual(px.parse_ip_addr(ip), {"wlan0": "192.168.0.83"})
        nm = "wlan0:connected:VM9611446\nwlan1:disconnected:--\nbad\n"
        self.assertEqual(px.parse_nmcli_dev(nm), {"wlan0": ("connected", "VM9611446"), "wlan1": ("disconnected", "")})
        gps = ['{"class":"VERSION"}', '{"class":"TPV","mode":3}', 'junk', '{"class":"SKY","satellites":[{"used":true},{"used":false},{"used":true}]}']
        self.assertEqual(px.parse_gpsd(gps), (3, 2))
        self.assertEqual(px.parse_gpsd(["x", None]), (None, None))
        self.assertEqual(px.parse_rtl_cmdline("rtl_sdr\x00-f\x00132100000\x00-s\x002400000"), 132.1)
        self.assertEqual(px.parse_rtl_cmdline("/usr/bin/rtl_sdr -f 98000000 -s 1"), 98.0)
        self.assertIsNone(px.parse_rtl_cmdline("python3 -f 5"))
        self.assertIsNone(px.parse_rtl_cmdline("rtl_sdr -f abc"))
        self.assertIsNone(px.parse_rtl_cmdline(""))

    def test_cards_and_histogram(self):
        radios = {"radios": [{"name": "wlan0", "kind": "onboard", "up": True}, {"name": "wlan1", "kind": "internal-usb", "up": False},
                             {"name": "flipper-marauder", "kind": "flipper-marauder", "up": True}, {"junk": 1}, 5]}
        cards = px.interface_cards(radios, {"wlan0": "10.0.0.5"}, {"wlan0": ("connected", "HomeNet")})
        self.assertEqual([c["name"] for c in cards], ["wlan0", "wlan1", "flipper-marauder"])
        self.assertEqual((cards[0]["detail"], cards[0]["ip"], cards[0]["level"]), ("HomeNet", "10.0.0.5", "good"))
        self.assertEqual((cards[1]["state"], cards[1]["detail"]), ("DOWN", "INTERNAL USB"))
        self.assertEqual(cards[2]["detail"], "Marauder board")
        self.assertEqual(px.interface_cards(None, None, None), [])
        hist = px.channel_histogram([{"channel": 1}, {"channel": 1}, {"channel": 11}, {"channel": 40}, {"channel": 108}, {"channel": 165}, {"x": 1}, 7])
        self.assertEqual(len(hist), 17)
        d = dict(hist)
        self.assertEqual((d["1"], d["11"], d["36"], d["100"], d["149"]), (2, 1, 1, 1, 1))
        self.assertEqual(px.channel_histogram(None)[0], ("1", 0))

    def test_battery(self):
        snap = {"power": {"capacity": 87, "status": "Charging", "power": 0.04}}
        self.assertEqual(px.battery_summary(snap), (87, "CHARGING", "+0.04W"))
        self.assertEqual(px.battery_summary({"power": {"capacity": 40, "status": "Discharging"}}), (40, "DISCHARGING", ""))
        self.assertEqual(px.battery_summary({})[0], None)


class RackTests(unittest.TestCase):
    SNAP = {"rails": {"GPS": "on", "LORA": "on", "SDR": "on", "USB": "on"}}

    def test_always_six_rows_in_order(self):
        for snap in (self.SNAP, {}, {"rails": {}}):
            self.assertEqual([r[0] for r in px.rack_rows(snap)], list(px.RACK_NAMES))

    def test_everything_off(self):
        rows = px.rack_rows({"rails": {"GPS": "off", "LORA": "off", "SDR": "off", "USB": "off"}})
        self.assertEqual([r[2] for r in rows], ["OFF", "OFF", "OFF", "OFF", "NONE", "NO SCAN"])
        self.assertEqual([r[1] for r in rows][:4], ["off"] * 4)

    def test_live_values(self):
        rows = dict((n, (lv, v)) for n, lv, v in px.rack_rows(self.SNAP, gps=(3, 9), sdr_mhz=132.1, mesh_up=True,
                                                               flip_mode="cli", wifi=(23, "Home")))
        self.assertEqual(rows["GPS"], ("good", "3D FIX 9S"))
        self.assertEqual(rows["LORA"], ("good", "MESH UP"))
        self.assertEqual(rows["SDR"], ("good", "132.100M"))
        self.assertEqual(rows["USB"], ("good", "HUB ON"))
        self.assertEqual(rows["FLIPPER"], ("good", "CLI"))
        self.assertEqual(rows["WIFI"], ("good", "23 AP"))

    def test_waiting_and_trouble(self):
        rows = dict((n, (lv, v)) for n, lv, v in px.rack_rows(self.SNAP, gps=(1, 0), mesh_up=False))
        self.assertEqual(rows["GPS"][0], "warn")
        self.assertEqual(rows["LORA"], ("warn", "NO SERVICE"))
        self.assertEqual(rows["SDR"], ("dim", "IDLE"))
        self.assertEqual(dict((n, v) for n, _, v in px.rack_rows(self.SNAP))["GPS"], "WAITING")

    def test_values_fit_the_row(self):
        for _n, _l, v in px.rack_rows(self.SNAP, gps=(3, 12), sdr_mhz=1234.567, mesh_up=True, flip_mode="bridge", wifi=(999, "x")):
            self.assertLessEqual(len(v), 12, v)          # 12 characters of 24 pixel Spleen is 144 pixels


class SoftKeyTests(unittest.TestCase):
    def test_there_is_a_sixth_key_and_the_first_five_keep_their_jobs(self):
        self.assertEqual(px.SOFT_KEYS[:5], (("F1", "SCAN"), ("F2", "RAILS"), ("F3", "TERM"), ("F4", "THEME"), ("F5", "FULL")))
        self.assertEqual(px.SOFT_KEYS[5], ("F6", "RECORD"))

    def test_every_label_ends_inside_its_key_at_every_size(self):
        for w, h in ((1256, 560), (1280, 664), (1100, 520), (900, 500)):
            keys = px.layout(w, h)["keys"]
            self.assertEqual(len(keys), len(px.SOFT_KEYS))
            for (x, y, kw, kh), (name, label) in zip(keys, px.SOFT_KEYS):
                name_x, label_x = px.key_text_x(kw)
                self.assertLessEqual(name_x + len(name) * 12 + 4, label_x, (w, name))      # 24 px font: 12 a letter
                self.assertLessEqual(label_x + len(label) * 12 + 4, kw, (w, label))

    def test_on_the_uconsole_screen_the_keys_keep_their_usual_spacing_and_room_for_the_arrow(self):
        kw = px.layout(1256, 560)["keys"][0][2]
        self.assertEqual(px.key_text_x(kw), (18, 66))
        longest = max(len(label) for _, label in px.SOFT_KEYS)
        self.assertLessEqual(66 + longest * 12 + 12 + 14, kw)       # the hover arrow sits 14 px from the right

    def test_the_sixth_key_is_found_under_the_pointer(self):
        keys = px.layout(1256, 560)["keys"]
        self.assertEqual(px.key_at(keys[5][0] + 3, keys[5][1] + 3, keys), 5)


class PowerLineTests(unittest.TestCase):
    def test_the_time_left_goes_after_the_watts(self):
        self.assertEqual(px.power_line("+3.10W", ("~2H 40M LEFT", "~2H 40M"), 22), "+3.10W ~2H 40M LEFT")

    def test_a_shorter_way_of_saying_it_is_used_when_the_long_one_does_not_fit(self):
        self.assertEqual(px.power_line("+12.10W", ("~12H 40M TO FULL", "~12H 40M"), 22), "+12.10W ~12H 40M")
        self.assertEqual(px.power_line("+3.10W", ("~2H 40M LEFT", "~2H 40M"), 15), "+3.10W ~2H 40M")

    def test_the_watts_are_never_dropped_to_make_room(self):
        self.assertEqual(px.power_line("+3.10W", ("WORKING IT OUT",), 19), "+3.10W")
        self.assertEqual(px.power_line("+3.10W", (), 22), "+3.10W")
        self.assertEqual(px.power_line("+3.10W", None, 22), "+3.10W")

    def test_with_no_watts_the_time_stands_alone(self):
        self.assertEqual(px.power_line("", ("~2H 40M LEFT", "~2H 40M"), 22), "~2H 40M LEFT")

    def test_room_on_the_line_is_what_the_big_percentage_leaves(self):
        width = px.layout(1256, 560)["power"][2]
        self.assertEqual(px.power_chars(width, 57), 22)
        self.assertEqual(px.power_chars(width, 100), 19)
        self.assertEqual(px.power_chars(width, None), 22)
        self.assertEqual(px.power_chars(40, 100), 0)
        self.assertLessEqual(len("+3.10W WORKING IT OUT"), px.power_chars(width, 57))


class TickerTests(unittest.TestCase):
    def test_a_running_session_is_announced_first_with_how_long_it_has_run(self):
        msgs = px.ticker_messages(dict(self.CTX, session=12 * 60 + 30))
        self.assertEqual(msgs[0], ("RECORDING 12 MIN", "EVERYTHING SESSION", "good", False))
        self.assertIn("GPS 3D FIX", [m[0] for m in msgs])        # and the rest still get their turn

    def test_no_session_no_recording_message(self):
        for ctx in (self.CTX, dict(self.CTX, session=None), {}):
            self.assertFalse(any("RECORDING" in m[0] for m in px.ticker_messages(ctx)))

    def test_a_warning_still_takes_the_ticker_from_a_session(self):
        low = px.ticker_messages(dict(self.CTX, pct=9, charging=False, session=600))
        self.assertEqual([m[0] for m in low], ["LOW BATTERY 9%"])

    def test_session_length_reads_in_minutes_then_hours(self):
        self.assertEqual(px.session_text(0), "<1 MIN")
        self.assertEqual(px.session_text(59), "<1 MIN")
        self.assertEqual(px.session_text(60), "1 MIN")
        self.assertEqual(px.session_text(59 * 60 + 59), "59 MIN")
        self.assertEqual(px.session_text(3900), "1H 05M")
        self.assertEqual(px.session_text(-5), "<1 MIN")            # the clock was set back: not a negative time
        for seconds in (0, 600, 3900, 100 * 3600):
            self.assertLessEqual(len("RECORDING " + px.session_text(seconds)), 22)

    CTX = {"pct": 87, "charging": True, "state": "charging", "temp": 50, "uptime": 4000, "load": 0.3, "ip": "10.0.0.5",
           "ssid": "Home", "gps": ("3D FIX", "9 SATELLITES", "good"), "wifi": (23, "Home"), "sdr": "7.100M"}

    def test_messages_cover_the_useful_things(self):
        heads = [m[0] for m in px.ticker_messages(self.CTX)]
        for want in ("GPS 3D FIX", "WIFI 23 AP", "SDR 7.100M", "UP 1h06", "10.0.0.5"):
            self.assertIn(want, heads)

    def test_gaps_are_filled_politely(self):
        heads = [m[0] for m in px.ticker_messages({})]
        self.assertNotIn("GPS OFF", heads)                    # what is off is not announced: the rack shows it
        self.assertIn("WIFI", heads)

    def test_only_what_is_on_or_plugged_in_gets_a_turn(self):
        quiet = [m[0] for m in px.ticker_messages({"uptime": 60})]
        for word in ("GPS", "MESH", "LORA", "BLUETOOTH", "FLIPPER", "SDR"):
            self.assertFalse(any(word in h for h in quiet), word)
        busy = px.ticker_messages(dict(self.CTX, lora=True, bt=0, flipper="cli"))
        heads = [m[0] for m in busy]
        for want in ("MESH UP", "BLUETOOTH READY", "FLIPPER CLI"):
            self.assertIn(want, heads)
        self.assertIn("BLUETOOTH 7 HEARD", [m[0] for m in px.ticker_messages({"bt": 7})])
        down = [m for m in px.ticker_messages({"lora": False}) if m[0] == "LORA ON"]
        self.assertEqual(down[0][2], "warn")                  # rail on, mesh service not running: worth a look
        for m in busy:
            self.assertLessEqual(len(m[1]), 26)

    def test_warnings_pin_themselves(self):
        low = px.ticker_messages(dict(self.CTX, pct=9, charging=False))
        self.assertEqual([m[0] for m in low], ["LOW BATTERY 9%"])
        hot = px.ticker_messages(dict(self.CTX, temp=84))
        self.assertTrue(all(m[3] for m in hot))
        self.assertEqual(px.pick_ticker(hot, 0), px.pick_ticker(hot, 999))
        self.assertTrue(px.ticker_messages(dict(self.CTX, throttled="under voltage"))[0][3])
        self.assertNotIn("LOW", px.ticker_messages(dict(self.CTX, pct=9, charging=True))[0][0])

    def test_rotation(self):
        msgs = px.ticker_messages(self.CTX)
        first = px.pick_ticker(msgs, 0)
        self.assertEqual(px.pick_ticker(msgs, px.TICKER_TICKS - 1), first)
        self.assertNotEqual(px.pick_ticker(msgs, px.TICKER_TICKS), first)
        self.assertEqual(px.pick_ticker([], 5), ("", "", "dim", False))


if __name__ == "__main__":
    unittest.main()


class ChartAndColourTests(unittest.TestCase):
    def test_temperature_runs_blue_to_green_to_red(self):
        self.assertIsNone(px.temp_colour(None))
        cold, cool, ok, warm, hot = (px.temp_colour(t) for t in (20, 45, 55, 68, 90))
        self.assertEqual((cold, cool, ok, warm, hot), ("#4aa3ff", "#3fe0d0", "#62e06b", "#ffd23f", "#ff3b3b"))
        mid = px.temp_colour(50)                       # between cyan and green
        self.assertNotIn(mid, (cool, ok))
        r, g, b = (int(mid[i:i + 2], 16) for i in (1, 3, 5))
        self.assertTrue(63 <= r <= 98 and g >= 224 and 107 <= b <= 208)

    def test_colour_is_monotonic_in_red_when_hot(self):
        reds = [int(px.temp_colour(t)[1:3], 16) for t in (55, 60, 65, 70, 75, 80)]
        self.assertEqual(reds, sorted(reds))

    def test_paper_mode_is_darker(self):
        dark, light = px.temp_colour(55, True), px.temp_colour(55, False)
        self.assertLess(int(light[3:5], 16), int(dark[3:5], 16))

    def test_history(self):
        h = []
        for v in range(100):
            h = px.push_history(h, v)
        self.assertEqual((len(h), h[0], h[-1]), (px.HISTORY, 10, 99))
        self.assertEqual(px.push_history([5], None), [5, 5])
        self.assertEqual(px.push_history([], None), [None])

    def test_spark_columns(self):
        cols = px.spark_columns([0, 50, 100], 0, 100, 40, 34)
        self.assertEqual([c[0] for c in cols], [28, 32, 36])           # newest at the right edge
        ys = [c[1] for c in cols]
        self.assertEqual(ys, sorted(ys, reverse=True))                 # a higher value is higher on screen
        self.assertTrue(all(y % 2 == 0 and 0 <= y <= 32 for y in ys))
        self.assertEqual(px.spark_columns([], 0, 100, 40, 34), [])
        self.assertEqual(px.spark_columns([None, None], 0, 100, 40, 34), [])
        self.assertEqual(px.spark_columns([5, 6], 10, 10, 40, 34), [])
        self.assertEqual(len(px.spark_columns(list(range(500)), 0, 500, 40, 34)), 10)

    def test_ranges_exist_for_every_vital(self):
        for label, *_ in px.vital_rows({"cpu": 1, "temp": 1, "mem": (1, 2, 50), "disk": 1}):
            self.assertIn(label, px.SPARK_RANGES)

    def test_rack_rows_that_are_rails(self):
        self.assertEqual([px.rack_toggle(i) for i in range(6)], ["GPS", "LORA", "SDR", "USB", None, None])
        self.assertIsNone(px.rack_toggle(-1))
        lay = px.layout(1256, 560)
        r = lay["rack"][2]
        self.assertEqual(px.rack_at(r[0] + 5, r[1] + 5, lay["rack"]), 2)
        self.assertIsNone(px.rack_at(0, 0, lay["rack"]))


class BlendTests(unittest.TestCase):
    def test_blend(self):
        self.assertEqual(px.blend("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(px.blend("#102030", "#102030", 0.7), "#102030")
        self.assertEqual(px.blend("#000000", "#ff0000", 5), "#ff0000")      # clamped
        self.assertEqual(px.blend("#000000", "#ff0000", -1), "#000000")



class CompanionRowTests(unittest.TestCase):
    """The rack's fifth row: the Flipper while one is plugged in, otherwise the Bluetooth radio when it is there."""
    SNAP = {"rails": {"GPS": "off", "LORA": "off", "SDR": "off", "USB": "on"}}

    def row(self, **kw):
        rows = px.rack_rows(self.SNAP, **kw)
        self.assertEqual(len(rows), 6)
        return rows[4]

    def test_a_flipper_that_is_plugged_in_has_the_row(self):
        self.assertEqual(self.row(flip_mode="cli", bt=3), ("FLIPPER", "good", "CLI"))
        self.assertEqual(self.row(flip_mode="bridge", bt=None), ("FLIPPER", "good", "BRIDGE"))

    def test_with_no_flipper_the_bluetooth_radio_has_it(self):
        self.assertEqual(self.row(bt=0), ("BLUETOOTH", "good", "READY"))
        self.assertEqual(self.row(bt=12), ("BLUETOOTH", "good", "12 HEARD"))

    def test_with_neither_it_says_no_flipper_as_before(self):
        self.assertEqual(self.row(), ("FLIPPER", "off", "NONE"))

    def test_the_rows_that_are_not_rails_open_their_tabs(self):
        self.assertEqual(px.rack_tab("FLIPPER"), "Flipper")
        self.assertEqual(px.rack_tab("BLUETOOTH"), "Bluetooth")
        self.assertIsNone(px.rack_tab("GPS"))


class HomeBatteryTests(unittest.TestCase):
    """The small pixel battery beside Home's percentage."""

    def roles(self, cells):
        return [c[4] for c in cells if c[4] in ("lit", "empty", "sweep", "pulse")]

    def test_it_is_the_size_the_layout_allows_for(self):
        self.assertEqual(px.battery_size(), (60, 40))
        w = max(c[0] + c[2] for c in px.battery_cells(50)) * px.BATTERY_UNIT
        h = max(c[1] + c[3] for c in px.battery_cells(50)) * px.BATTERY_UNIT
        self.assertEqual((w, h), px.battery_size())

    def test_bars_fill_with_the_percentage(self):
        self.assertEqual(self.roles(px.battery_cells(0)), ["empty"] * 5)
        self.assertEqual(self.roles(px.battery_cells(1)), ["lit"] + ["empty"] * 4)
        self.assertEqual(self.roles(px.battery_cells(45)), ["lit"] * 3 + ["empty"] * 2)
        self.assertEqual(self.roles(px.battery_cells(100)), ["lit"] * 5)
        self.assertEqual(self.roles(px.battery_cells(None)), ["empty"] * 5)

    def test_on_battery_nothing_moves_and_there_is_no_bolt(self):
        for phase in range(8):
            cells = px.battery_cells(45, False, phase)
            self.assertEqual(cells, px.battery_cells(45, False, 0))
            self.assertNotIn("bolt", [c[4] for c in cells])

    def test_while_charging_a_bright_bar_runs_along_and_a_bolt_is_shown(self):
        seen = {tuple(self.roles(px.battery_cells(45, True, phase))) for phase in range(12)}
        self.assertGreater(len(seen), 2)
        self.assertTrue(any("sweep" in s for s in seen))
        self.assertTrue(any("pulse" in s for s in seen))
        self.assertIn("bolt", [c[4] for c in px.battery_cells(45, True, 0)])

    def test_the_bolt_stays_inside_the_battery(self):
        for c in px.battery_cells(45, True, 0):
            if c[4] in ("bolt", "stroke"):
                self.assertTrue(2 <= c[0] <= 24 and 2 <= c[1] <= 17, c)

    def test_the_text_beside_it_gets_less_room(self):
        self.assertLess(px.power_chars(390, 45, False, 72), px.power_chars(390, 45))
        self.assertEqual(px.power_chars(390, 45, False, 72), (390 - 28 - 96 - 12 - 72) // 12)
