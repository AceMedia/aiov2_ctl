import re
import time
import unittest

import numpy as np

from ops_gui import notify, sense as s

PROC = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   39.  -71.  -256        0      0      0     24      0        0
 wlan1: 0000   10.  -85.  -256        0      0      0      0      0        0
"""
RNG = np.random.default_rng(11)


def drive(engine, readings, rate=5.0, t0=0.0, name="wlan0", step_every=1.0):
    """Feed readings at `rate` a second and step once a second; returns every event that happened."""
    events, per = [], max(1, int(rate * step_every))
    for i, v in enumerate(readings):
        t = t0 + i / rate
        engine.add(name, t, v, native=rate)
        if (i + 1) % per == 0:
            events += engine.step(t)
    return events


def still(seconds, rate=5.0, base=-60.0, sigma=0.5):
    return list(base + RNG.normal(0, sigma, int(seconds * rate)))


def moving(seconds, rate=5.0, base=-60.0):
    n = int(seconds * rate)
    t = np.arange(n) / rate
    return list(base + RNG.normal(0, 1.5, n) + 6 * np.sin(2 * np.pi * 1.0 * t))


def learned(engine=None, rate=5.0):
    engine = engine or s.Engine()
    drive(engine, still(s.CALIBRATE_SECONDS + 5, rate), rate)
    return engine


class ParseTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(s.parse_proc_wireless(PROC), {"wlan0": -71.0, "wlan1": -85.0})

    def test_bad_input(self):
        for bad in ("", None, "a\nb\n wlan0: 0 1. x", "a\nb\n wlan0: 0000 39. 12. -256"):
            self.assertEqual(s.parse_proc_wireless(bad), {})
        self.assertEqual(s.read_levels("/nonexistent/file"), {})

    def test_source_options_strongest_first(self):
        opts = s.source_options({"wlan0": -71.0, "wlan1": -60.0})
        self.assertEqual([o[0] for o in opts], ["wlan1", "wlan0"])
        self.assertEqual(opts[0][1], "wlan1  -60 dBm")
        self.assertEqual(s.source_options({}), [])


class HelperTests(unittest.TestCase):
    def test_chart_range_is_never_tiny(self):
        lo, hi = s.chart_range([-71, -71, -71])
        self.assertGreaterEqual(hi - lo, 8)
        self.assertEqual(s.chart_range([]), (-90.0, -40.0))
        self.assertEqual(s.chart_range([-80, -50]), (-82, -48))

    def test_heat_runs_from_dark_to_red(self):
        self.assertEqual(s.heat(-5), "#1d1724")
        self.assertEqual(s.heat(8), "#3fe0d0")
        self.assertEqual(s.heat(99), "#ff3b3b")
        reds = [int(s.heat(x)[1:3], 16) for x in (13, 15, 17, 20)]
        self.assertEqual(reds, sorted(reds))

    def test_event_text_and_csv(self):
        self.assertIn("movement started", s.event_text("movement_start", 0))
        self.assertIn("room learned", s.event_text("learned", 0))
        self.assertEqual(s.csv_line(10.0, "quiet", 1.234, {"b": None, "a": -60.0}), "10.00,quiet,1.23,-60.0,")

    def test_the_notification_source_exists_and_is_off_by_default(self):
        self.assertIn("sense", dict(notify.SOURCES))
        self.assertFalse(notify.config({})["sense"])


class EngineTests(unittest.TestCase):
    def test_it_learns_first_and_says_so_once(self):
        e = s.Engine()
        events = drive(e, still(s.CALIBRATE_SECONDS + 5))
        self.assertEqual(events.count("learned"), 1)
        self.assertEqual(e.state, "quiet")

    def test_progress_while_learning(self):
        e = s.Engine()
        drive(e, still(15))
        self.assertEqual(e.state, "calibrating")
        self.assertAlmostEqual(e.progress, 0.5, delta=0.1)
        self.assertEqual(e.level(), 0.0)

    def test_a_still_room_never_alarms(self):
        e = learned()
        events = drive(e, still(120), t0=40)
        self.assertEqual([x for x in events if x != "learned"], [])
        self.assertEqual(e.state, "quiet")

    def test_movement_starts_and_ends(self):
        e = learned()
        events = drive(e, moving(20) + still(60), t0=40)
        self.assertIn("movement_start", events)
        self.assertIn("presence_start", events)
        self.assertIn("movement_end", events)
        self.assertLess(events.index("movement_start"), events.index("movement_end"))

    def test_after_movement_it_reads_recent_then_quiet(self):
        e = learned()
        drive(e, moving(20), t0=40)
        self.assertEqual(e.state, "movement")
        drive(e, still(6), t0=60)
        self.assertIn(e.state, ("recent", "movement"))
        events = drive(e, still(60), t0=66)
        self.assertEqual(e.state, "quiet")
        self.assertIn("presence_end", events)

    def test_a_blip_does_not_count(self):
        e = learned()
        series = still(20) + [-50.0] + still(20)
        events = drive(e, series, t0=40)
        self.assertNotIn("movement_start", events)

    def test_sensitivity_moves_the_lines(self):
        globals()["RNG"] = np.random.default_rng(21)          # fixed, so this does not depend on which tests ran first
        mild = lambda: list(-60 + 0.8 * np.sin(2 * np.pi * 1.0 * np.arange(0, 20, 0.2)) + RNG.normal(0, 0.4, 100))
        low, high = learned(s.Engine("Low")), learned(s.Engine("High"))
        a = drive(low, mild(), t0=40)
        b = drive(high, mild(), t0=40)
        self.assertNotIn("movement_start", a)
        self.assertIn("movement_start", b)

    def test_changing_sensitivity_on_the_fly(self):
        e = learned()
        e.set_sensitivity("High")
        self.assertEqual(e.offset, s.SENSITIVITY["High"])
        self.assertEqual(e.motion.on_db, s.dsp.MOTION_ON_DB + s.SENSITIVITY["High"])
        e.set_sensitivity("nonsense")
        self.assertEqual(e.offset, 0.0)

    def test_several_links_are_scored_separately(self):
        e = s.Engine()
        for i in range(int((s.CALIBRATE_SECONDS + 5) * 5)):
            t = i / 5
            e.add("ap1", t, -60 + RNG.normal(0, 0.5))
            e.add("ap2", t, -70 + RNG.normal(0, 0.5))
            if (i + 1) % 5 == 0:
                e.step(t)
        for i in range(100):
            t = 40 + i / 5
            e.add("ap1", t, -60 + 6 * np.sin(2 * np.pi * i / 5) + RNG.normal(0, 1.5))
            e.add("ap2", t, -70 + RNG.normal(0, 0.5))
            if (i + 1) % 5 == 0:
                e.step(t)
        dbs = e.link_dbs()
        self.assertGreater(dbs["ap1"], dbs["ap2"] + 6)

    def test_reset_starts_again(self):
        e = learned()
        e.reset()
        self.assertEqual((e.state, e.links, e.db), ("calibrating", {}, 0.0))

    def test_missing_readings_are_harmless(self):
        e = learned()
        events = drive(e, [None] * 100, t0=40)
        self.assertEqual(events, [])

    def test_reliability(self):
        e = learned()
        self.assertGreater(e.reliability(), 0.9)
        self.assertEqual(s.Engine().reliability(), 0.0)


class ActivityTests(unittest.TestCase):
    def test_counts_movement_and_gaps(self):
        log = s.ActivityLog()
        self.assertEqual(log.text(0.0), "Still learning the room.")
        log.update("quiet", 0.0)
        self.assertIn("Nothing has moved", log.text(1.0))
        log.update("movement", 10.0)
        self.assertEqual(log.text(12.0).split(".")[0], "Moving now")
        log.update("quiet", 20.0)
        log.update("quiet", 50.0)
        txt = log.text(50.0)
        self.assertIn("Nothing moving for 30s", txt)
        self.assertIn("1 stretch of movement, 10s in all", txt)

    def test_old_movement_ages_out(self):
        log = s.ActivityLog()
        log.update("movement", 0.0)
        log.update("quiet", 5.0)
        log.update("quiet", 700.0)
        self.assertIn("0 stretches", log.text(700.0))
        self.assertIn("11 min", log.text(700.0))

    def test_names_the_most_disturbed_link(self):
        log = s.ActivityLog()
        log.update("quiet", 1.0)
        self.assertIn("Most disturbed: ap b (+9 dB)", log.text(2.0, {"ap a": 1.0, "ap b": 9.0}))
        self.assertNotIn("Most disturbed", log.text(2.0, {"ap a": 1.0}))

    def test_the_engine_keeps_one(self):
        e = learned()
        drive(e, moving(15) + still(30), t0=40)
        self.assertGreater(e.activity.moved_seconds(e.last_step), 3)


class HonestyTests(unittest.TestCase):
    def test_it_only_listens(self):
        import inspect
        code = inspect.getsource(s).split('"""')[2]
        code = code.replace("in monitor mode", "")
        for word in ("socket", "nmcli", "scan(", "inject", "deauth", "sendto"):
            self.assertNotIn(word, code)
        # the only things it runs: the passive listener, its restore, and a read of the connected channel
        for text in re.findall(r'\[("[^\]]*)\]', code):
            if "iw" in text or "aiov2_ctl" in text:
                self.assertTrue("--wifi-listen" in text or '"link"' in text, text)

    def test_no_heart_rate_or_pose_are_offered(self):
        import inspect
        src = inspect.getsource(s)
        self.assertIn("not offered", src)
        self.assertNotIn("def breathing", src)
        self.assertNotIn("heart_rate", src)
        self.assertNotIn("skeleton", src)

    def test_the_recording_is_private(self):
        import inspect
        self.assertIn("0o600", inspect.getsource(s))


class AirTests(unittest.TestCase):
    def rec(self, t, links):
        return {"t": float(t), "frames": sum(n for n, _ in links.values()), "rssi": -70.0,
                "links": {m: [n, d] for m, (n, d) in links.items()}}

    def test_parse_line(self):
        good = '{"t": 5.5, "frames": 3, "rssi": -70.0, "links": {"aa:bb:cc:dd:ee:ff": [3, -70.0]}}'
        self.assertEqual(s.parse_air_line(good)["frames"], 3)
        for bad in ("", "nope", "[]", '{"t": 1}', '{"links": {}}', None, '{"t": "x", "links": {}}'):
            self.assertIsNone(s.parse_air_line(bad))

    def test_channel_from_iw(self):
        self.assertEqual(s.air_channel("Connected to x\n\tfreq: 5180\n\tchannel 36 (5180 MHz)"), 36)
        self.assertEqual(s.air_channel("channel 6 (2437 MHz), width: 20 MHz"), 6)
        self.assertEqual(s.air_channel("channel 34 (5170 MHz)"), 6)          # not one the sudoers rules allow
        self.assertEqual(s.air_channel(""), 6)
        self.assertEqual(s.air_channel(None), 6)

    def test_command_matches_the_sudoers_rule(self):
        args = s.air_args(11)
        self.assertEqual(" ".join(args[3:]), "--wifi-listen --iface wlan1 --channel 11 --seconds 3600 --json")
        self.assertEqual(args[:3], ["sudo", "-n", "aiov2_ctl"])
        self.assertEqual(" ".join(s.AIR_RESTORE[3:]), "--wifi-listen --restore --iface wlan1")

    def test_steady_senders_are_chosen_then_fixed(self):
        air = s.AirLinks(keep=2, settle=5)
        steady = {"aa:aa:aa:aa:aa:01": (4, -60.0), "aa:aa:aa:aa:aa:02": (3, -70.0), "aa:aa:aa:aa:aa:03": (1, -80.0)}
        for i in range(4):
            self.assertEqual(air.feed(self.rec(i, steady)), {})
        got = air.feed(self.rec(4, steady))
        self.assertEqual(sorted(got), ["ap aa:aa:01", "ap aa:aa:02"])
        later = dict(steady, **{"aa:aa:aa:aa:aa:09": (50, -40.0)})          # a loud newcomer does not take over
        self.assertEqual(sorted(air.feed(self.rec(5, later))), ["ap aa:aa:01", "ap aa:aa:02"])
        self.assertEqual(air.feed(self.rec(6, {"aa:aa:aa:aa:aa:01": (2, -61.0)})), {"ap aa:aa:01": -61.0})

    def test_a_custom_label_names_a_known_sender_instead_of_the_bare_address(self):
        from ops_gui import registry
        reg = registry.Registry()
        reg.see("aa:aa:aa:aa:aa:01", ssid="HomeNet")
        air = s.AirLinks(keep=2, settle=3, label_for=reg.label)
        steady = {"aa:aa:aa:aa:aa:01": (4, -60.0), "aa:aa:aa:aa:aa:02": (3, -70.0)}
        for i in range(2):
            air.feed(self.rec(i, steady))
        got = air.feed(self.rec(2, steady))
        self.assertEqual(sorted(got), ["HomeNet", "ap aa:aa:02"])

    def test_nobody_steady_keeps_waiting(self):
        air = s.AirLinks(keep=2, settle=3)
        for i in range(10):
            quiet = {"aa:aa:aa:aa:aa:01": (1, -60.0)} if i % 5 == 0 else {}      # heard now and then, not steady
            self.assertEqual(air.feed(self.rec(i, quiet)), {})
        self.assertIsNone(air.pinned)

    def test_air_links_drive_the_engine(self):
        e, air = s.Engine(), s.AirLinks(keep=1, settle=3)
        rng = np.random.default_rng(3)
        events = []
        for i in range(160):
            rec = self.rec(i, {"aa:aa:aa:aa:aa:01": (5, float(-60 + rng.normal(0, 0.5)))})
            for name, dbm in air.feed(rec).items():
                e.add(name, rec["t"], dbm, native=1.0)
            events += e.step(rec["t"])
        self.assertIn("learned", events)
        self.assertEqual(e.state, "quiet")


    def test_readings_use_every_frame_when_sent(self):
        air = s.AirLinks(keep=1, settle=1)
        rec = {"t": 100.0, "links": {"aa:aa:aa:aa:aa:01": [8, -60.0, [[-0.9, -61], [-0.5, -59], [-0.1, -60]]]}}
        air.feed(rec)
        pts = air.readings(rec)["ap aa:aa:01"]
        self.assertEqual([round(t, 1) for t, _ in pts], [99.1, 99.5, 99.9])
        self.assertEqual(air.last["ap aa:aa:01"], -60.0)

    def test_readings_fall_back_to_the_average(self):
        air = s.AirLinks(keep=1, settle=1)
        rec = {"t": 50.0, "links": {"aa:aa:aa:aa:aa:01": [8, -60.0]}}
        air.feed(rec)
        self.assertEqual(air.readings(rec)["ap aa:aa:01"], [(50.0, -60.0)])

    def test_a_sharp_change_then_stillness_is_passed_on_as_just_that(self):
        e = learned()
        e.sharp = type("Now", (), {"update": lambda self, db, now: "sharp_then_still"})()
        self.assertIn("sharp_then_still", e.step(e.last_step + 1.0))
        self.assertFalse(hasattr(e, "fall"))
        self.assertFalse(hasattr(e.activity, "falls"))

    def test_feed_reads_records_and_always_restores(self):
        import io
        calls = []

        class Proc:
            stdout = io.StringIO('{"t": 1.0, "frames": 1, "rssi": -60, "links": {}}\njunk\n')
            stderr = io.StringIO("")
            def __init__(self): self.stopped = False
            def poll(self): return None if not self.stopped else 0
            def terminate(self): self.stopped = True
            def wait(self, t=None): return 0
        proc = Proc()
        feed = s.AirFeed(6, runner=lambda *a, **k: proc)
        for _ in range(50):
            if feed.queue.qsize():
                break
            time.sleep(0.02)
        self.assertEqual(len(feed.records()), 1)
        feed.stop(runner=lambda cmd, **k: calls.append(cmd))
        self.assertTrue(proc.stopped)
        self.assertEqual(calls, [s.AIR_RESTORE])

    def test_the_air_source_never_transmits(self):
        import inspect
        src = inspect.getsource(s.AirFeed) + inspect.getsource(s.AirLinks)
        for word in ("deauth", "inject", "sendto", "--yes"):
            self.assertNotIn(word, src)


class OneSenderTests(unittest.TestCase):
    """Following just one access point, picked in Survey, instead of the steadiest few."""

    AP = "aa:bb:cc:00:11:22"

    def rec(self, t, links):
        return {"t": float(t), "frames": 1, "rssi": -70.0, "links": {m: [n, d] for m, (n, d) in links.items()}}

    def test_a_target_needs_a_proper_address_and_an_allowed_channel(self):
        self.assertEqual(s.air_target(self.AP, 6), (self.AP, 6))
        self.assertEqual(s.air_target(" AA:BB:CC:00:11:22 ", "36"), (self.AP, 36))
        for bssid, channel in ((self.AP, None), (self.AP, "-"), (self.AP, 52), (self.AP, 0), ("", 6), (None, 6),
                               ("aa:bb:cc", 6), ("zz:bb:cc:00:11:22", 6)):
            self.assertIsNone(s.air_target(bssid, channel), (bssid, channel))

    def test_every_channel_a_target_can_have_is_one_the_sudoers_rules_allow(self):
        for channel in range(0, 200):
            self.assertEqual(s.air_target(self.AP, channel) is not None, channel in s.AIR_CHANNELS)

    def test_a_source_name_carries_the_channel_and_the_one_sender(self):
        self.assertEqual(s.air_source(6), "air:6")
        self.assertEqual(s.air_source(36, self.AP), "air:36:" + self.AP)
        self.assertEqual(s.parse_air_source("air:6"), (6, None))
        self.assertEqual(s.parse_air_source("air:36:" + self.AP), (36, self.AP))

    def test_something_that_is_not_an_air_source_is_refused(self):
        for bad in ("wlan0", "", None, "air:", "air:x"):
            with self.assertRaises(ValueError):
                s.parse_air_source(bad)

    def test_one_sender_still_runs_the_same_listen_command(self):
        channel, _ = s.parse_air_source(s.air_source(11, self.AP))
        self.assertEqual(" ".join(s.air_args(channel)[3:]), "--wifi-listen --iface wlan1 --channel 11 --seconds 3600 --json")

    def test_the_chosen_sender_is_followed_from_the_first_record(self):
        air = s.AirLinks(only=self.AP)
        got = air.feed(self.rec(1, {self.AP: (2, -55.0), "aa:aa:aa:aa:aa:01": (40, -40.0)}))
        self.assertEqual(got, {"ap 00:11:22": -55.0})

    def test_a_louder_steadier_sender_never_replaces_the_chosen_one(self):
        air = s.AirLinks(only=self.AP, settle=3)
        for t in range(1, 30):
            got = air.feed(self.rec(t, {self.AP: (1, -80.0), "aa:aa:aa:aa:aa:01": (50, -40.0)}))
            self.assertEqual(list(got), ["ap 00:11:22"])
        self.assertEqual(air.pinned, [self.AP])

    def test_a_record_without_the_chosen_sender_gives_no_reading(self):
        air = s.AirLinks(only=self.AP)
        rec = self.rec(1, {"aa:aa:aa:aa:aa:01": (50, -40.0)})
        self.assertEqual(air.feed(rec), {})
        self.assertEqual(air.readings(rec), {})

    def test_the_chosen_sender_is_matched_whatever_case_the_listener_writes(self):
        air = s.AirLinks(only=self.AP.upper())
        rec = self.rec(5, {"AA:BB:CC:00:11:22": (3, -61.0)})
        self.assertEqual(air.feed(rec), {"ap 00:11:22": -61.0})
        self.assertEqual(air.readings(rec), {"ap 00:11:22": [(5.0, -61.0)]})

    def test_the_chosen_sender_is_shown_by_its_network_name_when_known(self):
        air = s.AirLinks(only=self.AP, label_for=lambda mac: "HomeNet")
        self.assertEqual(air.feed(self.rec(1, {self.AP: (3, -61.0)})), {"HomeNet": -61.0})

    def test_the_chosen_sender_drives_the_engine(self):
        e, air = s.Engine(), s.AirLinks(only=self.AP)
        events = []
        for t in range(1, 80):
            rec = self.rec(t, {self.AP: (10, -60.0 + 0.1 * (t % 2))})
            air.feed(rec)
            for name, pts in air.readings(rec).items():
                for when, dbm in pts:
                    e.add(name, when, dbm, native=1.0)
            events += e.step(rec["t"])
        self.assertIn("learned", events)
        self.assertEqual(list(e.links), ["ap 00:11:22"])

    def test_the_air_source_is_blocked_when_the_radio_is_not_there(self):
        what, todo = s.air_blocker(False, {})
        self.assertIn("not there", what)
        self.assertIn("Switch the USB rail on", todo)

    def test_the_air_source_is_blocked_when_its_radio_is_connected(self):
        what, todo = s.air_blocker(True, {"wlan1": -40.0})
        self.assertIn("wlan1 is connected", what)
        self.assertIn("Disconnect it first", todo)

    def test_the_air_source_is_free_otherwise(self):
        self.assertIsNone(s.air_blocker(True, {}))
        self.assertIsNone(s.air_blocker(True, {"wlan0": -40.0}))
        self.assertIsNone(s.air_blocker(True, None))

    def test_one_sender_is_named_by_its_network_and_channel_in_notices(self):
        self.assertEqual(s.source_title("air:6:" + self.AP, lambda mac: "HomeNet"), "HomeNet (channel 6)")
        self.assertEqual(s.source_title("air:6:" + self.AP), "ap 00:11:22 (channel 6)")
        self.assertEqual(s.source_title("air:6"), "air:6")
        self.assertEqual(s.source_title("wlan0"), "wlan0")

    def test_waiting_for_a_sender_says_why_it_may_never_be_heard(self):
        text = s.waiting_text("HomeNet", 6)
        self.assertIn("Nothing heard from HomeNet on channel 6", text)
        self.assertIn("another channel", text)
        self.assertIn("Nothing is sent", text)

    def test_following_one_sender_never_transmits(self):
        import inspect
        src = "".join(inspect.getsource(f) for f in (s.air_target, s.air_source, s.parse_air_source, s.air_blocker,
                                                      s.source_title, s.waiting_text, s.AirLinks))
        for word in ("deauth", "inject", "sendto", "--yes", "probe", "handshake", "pmkid"):
            self.assertNotIn(word, src.lower())


if __name__ == "__main__":
    unittest.main()
