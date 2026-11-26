import json
import os
import tempfile
import unittest

from ops_gui import fieldlog as fl

TPV = ('{"class":"TPV","mode":3,"time":"2026-11-25T10:00:00.000Z","lat":53.1,"lon":-1.2,"altHAE":251.3,'
       '"altMSL":203.9,"speed":0.4,"track":88.0,"climb":0.1,"eph":4.2,"epv":7.9}')
SKY = ('{"class":"SKY","hdop":0.9,"vdop":1.4,"pdop":1.7,"nSat":3,"uSat":2,"satellites":['
       '{"PRN":1,"ss":41.0,"used":true},{"PRN":2,"ss":35.0,"used":true},{"PRN":3,"ss":44.0,"used":false}]}')


class GpsLineTests(unittest.TestCase):
    """What one GPS line of the field log holds."""

    def test_the_newest_report_of_each_kind_is_picked_out_of_gpsd_lines(self):
        lines = ["junk", '{"class":"TPV","mode":1}', SKY, TPV]
        self.assertEqual(fl.newest(lines, "TPV")["mode"], 3)
        self.assertEqual(fl.newest(lines, "SKY")["uSat"], 2)
        self.assertIsNone(fl.newest([], "TPV"))
        self.assertIsNone(fl.newest(None, "SKY"))

    def test_both_altitudes_and_the_error_estimates_are_kept(self):
        f = fl.gps_fields(json.loads(TPV), json.loads(SKY))
        self.assertEqual((f["alt_msl"], f["alt_hae"]), (203.9, 251.3))
        self.assertEqual((f["eph"], f["epv"]), (4.2, 7.9))
        self.assertEqual((f["lat"], f["lon"], f["mode"]), (53.1, -1.2, 3))

    def test_satellites_are_summed_up_not_listed(self):
        f = fl.gps_fields(json.loads(TPV), json.loads(SKY))
        self.assertEqual((f["seen"], f["used"]), (3, 2))
        self.assertEqual((f["snr_used_max"], f["snr_used_mean"], f["snr_max"]), (41.0, 38.0, 44.0))
        self.assertEqual(f["hdop"], 0.9)
        self.assertNotIn("satellites", f)

    def test_an_older_receiver_that_only_says_alt_still_gets_an_altitude(self):
        self.assertEqual(fl.gps_fields({"mode": 3, "alt": 120.0}, None)["alt_msl"], 120.0)

    def test_no_fix_is_written_as_no_fix_and_silence_as_silence(self):
        f = fl.gps_fields({"class": "TPV", "mode": 1}, None)
        self.assertEqual(f["mode"], 1)
        self.assertIsNone(f["lat"])
        self.assertEqual(fl.gps_fields(None, None), {"answer": False})
        self.assertEqual(fl.sky_summary(None), {})


class FixWatchTests(unittest.TestCase):
    """The moments worth a line of their own."""

    def kinds(self, events):
        return [k for k, _ in events]

    def test_the_first_fix_says_how_long_it_took_from_the_rail_coming_on(self):
        w = fl.FixWatch()
        self.assertEqual(self.kinds(w.update(True, None, 100.0)), ["gps_rail_on"])
        self.assertEqual(w.update(True, 1, 110.0), [])
        events = w.update(True, 3, 142.5)
        self.assertEqual(events, [("gps_first_fix", {"seconds_since_rail_on": 42.5, "mode": 3})])
        self.assertEqual(w.update(True, 3, 150.0), [])                    # still fixed: nothing new to say

    def test_a_fix_lost_and_found_again_is_not_called_the_first(self):
        w = fl.FixWatch()
        w.update(True, 3, 10.0)
        self.assertEqual(self.kinds(w.update(True, 1, 20.0)), ["gps_fix_lost"])
        self.assertEqual(self.kinds(w.update(True, 2, 30.0)), ["gps_fix_back"])

    def test_the_rail_going_off_starts_the_count_again(self):
        w = fl.FixWatch()
        w.update(True, 3, 10.0)
        self.assertEqual(self.kinds(w.update(False, None, 20.0)), ["gps_rail_off"])
        self.assertEqual(w.update(False, None, 25.0), [])
        self.assertEqual(self.kinds(w.update(True, 3, 30.0)), ["gps_rail_on", "gps_first_fix"])


class StateAndKernelTests(unittest.TestCase):
    def test_the_state_line_holds_rails_battery_heat_and_tabs(self):
        f = fl.state_fields({"rails": {"GPS": "on", "USB": "off"}}, {"temp": 48.2, "cpu": 12, "load": 0.4},
                            ["Home", "GPS"], "GPS", None, True, (87, "DISCHARGING", "-3.10W"))
        self.assertEqual(f["rails"], {"GPS": "on", "USB": "off"})
        self.assertEqual((f["battery"], f["power"], f["watts"]), (87, "DISCHARGING", "-3.10W"))
        self.assertEqual((f["temp"], f["tab"], f["mesh_up"]), (48.2, "GPS", True))
        self.assertEqual(fl.state_fields(None)["rails"], {})

    def test_only_the_kernel_lines_about_radios_power_and_faults_are_kept(self):
        text = ("2026-11-25T10:00:01 kernel: Bluetooth: hci1: command tx timeout\n"
                "2026-11-25T10:00:02 kernel: usb 1-1.4.4: reset high-speed USB device number 9 using dwc2\n"
                "2026-11-25T10:00:03 kernel: audit: type=1400 something dull\n"
                "2026-11-25T10:00:04 kernel: Under-voltage detected!\n")
        kept = fl.kernel_lines(text)
        self.assertEqual(len(kept), 3)
        self.assertFalse(any("audit" in line for line in kept))
        self.assertEqual(fl.kernel_lines(""), [])
        self.assertEqual(len(fl.kernel_lines("usb 1-1: x\n" * 100, limit=5)), 5)


class FieldLogFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.on, self.now = True, 1795600000.0
        self.log = fl.FieldLog(lambda: self.on, self.dir.name, clock=lambda: self.now)

    def read(self):
        out = []
        for path in fl.files(self.dir.name):
            with open(path) as f:
                out += [json.loads(line) for line in f]
        return out

    def test_nothing_is_written_while_logging_is_off(self):
        self.on = False
        self.assertFalse(self.log.event("gps", mode=3))
        self.assertEqual(fl.files(self.dir.name), [])

    def test_each_event_is_one_json_line_with_the_time_and_no_empty_fields(self):
        self.assertTrue(self.log.event("gps", mode=3, lat=53.1, lon=None, note=""))
        self.log.event("activity", text="Bluetooth scan started")
        rows = self.read()
        self.assertEqual([r["k"] for r in rows], ["gps", "activity"])
        self.assertEqual(rows[0]["ts"], self.now)
        self.assertIn("T", rows[0]["t"])
        self.assertNotIn("lon", rows[0])
        self.assertNotIn("note", rows[0])

    def test_the_file_is_private_and_named_for_the_day(self):
        self.log.event("state", rails={})
        path = fl.files(self.dir.name)[0]
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertRegex(os.path.basename(path), r"^field-\d{8}\.jsonl$")

    def test_a_new_day_gets_a_new_file(self):
        self.log.event("state", rails={})
        self.now += 2 * 86400
        self.log.event("state", rails={})
        self.assertEqual(len(fl.files(self.dir.name)), 2)

    def test_a_full_file_says_so_once_and_then_stops_growing(self):
        log = fl.FieldLog(lambda: True, self.dir.name, clock=lambda: self.now, cap=200)
        for i in range(30):
            log.event("gps", mode=3, n=i)
        rows = self.read()
        self.assertEqual([r["k"] for r in rows].count("log_full"), 1)
        self.assertEqual(rows[-1]["k"], "log_full")
        size = os.path.getsize(fl.files(self.dir.name)[0])
        self.assertFalse(log.event("gps", mode=3))
        self.assertEqual(os.path.getsize(fl.files(self.dir.name)[0]), size)

    def test_something_that_is_not_json_is_written_as_text_rather_than_lost(self):
        self.assertTrue(self.log.event("odd", thing=object))
        self.assertEqual(self.read()[0]["k"], "odd")

    def test_a_folder_that_cannot_be_written_does_not_raise(self):
        log = fl.FieldLog(lambda: True, "/proc/nope/never", clock=lambda: self.now)
        self.assertFalse(log.event("gps", mode=3))


class KernelRepeatTests(unittest.TestCase):
    def test_a_line_on_the_boundary_of_two_looks_is_written_once(self):
        seen = []
        first = fl.kernel_lines("10:00:01 usb 1-1.3: new device\n10:00:02 hci1: command tx timeout\n", seen=seen)
        second = fl.kernel_lines("10:00:02 hci1: command tx timeout\n10:00:09 usb 1-1.3: USB disconnect\n", seen=seen)
        self.assertEqual(len(first), 2)
        self.assertEqual(second, ["10:00:09 usb 1-1.3: USB disconnect"])

    def test_what_has_been_seen_does_not_grow_without_end(self):
        seen = []
        for i in range(1000):
            fl.kernel_lines(f"line {i} usb 1-1: x\n", seen=seen)
        self.assertLessEqual(len(seen), 400)


if __name__ == "__main__":
    unittest.main()
