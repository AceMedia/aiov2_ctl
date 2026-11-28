"""The Everything tab: which sources a session uses, what a reading holds, the chart's numbers, the file, the review."""
import ast
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from ops_gui import data, everything as ev, reports

T0 = 1795000000.0
ALL_ON = {"rails": {"GPS": "on", "LORA": "on", "SDR": "on", "USB": "on"}}
TPV = {"class": "TPV", "mode": 3, "lat": 53.1, "lon": -1.2, "altMSL": 203.9, "eph": 4.2}
SKY = {"class": "SKY", "hdop": 0.9, "nSat": 3, "uSat": 2,
       "satellites": [{"PRN": 1, "ss": 41.0, "used": True}, {"PRN": 2, "ss": 35.0, "used": True}, {"PRN": 3, "ss": 44.0}]}


def everything_usable(**changes):
    facts = dict(bt_adapter=True, air_there=True, mesh_service=True, mesh_connected=True, sdr_mhz=433.92)
    facts.update(changes)
    return facts


def sample(at, **sources):
    rec = {"k": "sample", "t": ev.stamp(T0 + at), "ts": T0 + at}
    rec.update(sources)
    return rec


def session_of(*records):
    s = ev.Session()
    s.add({"k": "start", "t": ev.stamp(T0), "ts": T0, "v": 1, "every": 5.0, "sources": {}})
    for rec in records:
        s.add(rec)
    return s


class AvailabilityTests(unittest.TestCase):
    """Which sources can be read right now. Nothing is ever switched on to make one available."""

    def test_with_every_rail_on_and_every_service_up_every_source_is_usable(self):
        avail = ev.availability(ALL_ON, **everything_usable())
        self.assertEqual({k for k, (ok, _why) in avail.items() if ok}, {"wifi", "bt", "gps", "mesh", "sdr", "sys"})

    def test_with_nothing_on_only_the_uconsole_itself_is_usable_and_each_other_source_says_why(self):
        avail = ev.availability({"rails": {"GPS": "off", "LORA": "off", "SDR": "off", "USB": "off"}})
        self.assertEqual([k for k, (ok, _why) in avail.items() if ok], ["sys"])
        self.assertEqual(avail["gps"][1], "the GPS rail is off")
        self.assertEqual(avail["mesh"][1], "the LoRa rail is off")
        self.assertEqual(avail["sdr"][1], "the SDR rail is off")
        self.assertIn("USB rail is off", avail["wifi"][1])
        self.assertIn("USB rail is off", avail["bt"][1])

    def test_no_status_at_all_is_treated_as_everything_off(self):
        for snap in (None, {}, {"rails": None}):
            self.assertEqual([k for k, (ok, _w) in ev.availability(snap).items() if ok], ["sys"])

    def test_wifi_is_not_usable_while_wlan1_is_connected_or_sense_is_listening_on_it(self):
        self.assertEqual(ev.availability(ALL_ON, **everything_usable(air_connected=True))["wifi"],
                         (False, "wlan1 is connected to a network"))
        self.assertEqual(ev.availability(ALL_ON, **everything_usable(air_in_use=True))["wifi"],
                         (False, "Sense is listening on wlan1"))

    def test_the_mesh_is_only_read_when_its_service_is_already_running_and_the_app_is_already_connected(self):
        self.assertEqual(ev.availability(ALL_ON, **everything_usable(mesh_service=False))["mesh"],
                         (False, "the mesh service is not running"))
        self.assertEqual(ev.availability(ALL_ON, **everything_usable(mesh_connected=False))["mesh"],
                         (False, "the app is not connected to the mesh"))

    def test_the_sdr_is_only_read_while_something_is_already_receiving(self):
        ok, why = ev.availability(ALL_ON, **everything_usable(sdr_mhz=None))["sdr"]
        self.assertFalse(ok)
        self.assertIn("nothing is receiving", why)


class ReconcileTests(unittest.TestCase):
    """What a session does about the two scans: it starts what it may, and only ever stops what it started."""

    def setUp(self):
        self.usable = ev.availability(ALL_ON, **everything_usable())
        self.limits = {"wifi": 3600, "bt": 600}

    def test_at_the_start_both_scans_are_started_when_both_radios_are_usable(self):
        plan = ev.reconcile(self.usable, {"wifi": False, "bt": False}, set(), {})
        self.assertEqual(plan["start"], ["wifi", "bt"])

    def test_a_source_whose_rail_is_off_is_not_started(self):
        avail = ev.availability({"rails": {"USB": "off"}})
        plan = ev.reconcile(avail, {"wifi": False, "bt": False}, set(), {})
        self.assertEqual(plan["start"], [])
        self.assertEqual(plan["owned"], set())

    def test_a_scan_that_is_already_running_is_not_started_again_and_not_taken_over(self):
        plan = ev.reconcile(self.usable, {"wifi": True, "bt": False}, set(), {})
        self.assertEqual(plan["start"], ["bt"])
        self.assertNotIn("wifi", plan["owned"])

    def test_a_scan_is_picked_up_when_its_radio_appears_during_the_session(self):
        was = {"wifi": False, "bt": False}
        plan = ev.reconcile(self.usable, {"wifi": False, "bt": False}, set(), was)
        self.assertEqual(plan["start"], ["wifi", "bt"])

    def test_a_radio_that_was_usable_all_along_and_is_not_ours_is_left_alone(self):
        was = {"wifi": True, "bt": True}
        plan = ev.reconcile(self.usable, {"wifi": False, "bt": False}, set(), was)
        self.assertEqual((plan["start"], plan["notes"]), ([], []))

    def test_a_scan_the_session_started_is_dropped_with_the_reason_when_its_radio_goes_away(self):
        avail = ev.availability({"rails": {"USB": "off"}})
        plan = ev.reconcile(avail, {"wifi": False, "bt": False}, {"wifi", "bt"}, {"wifi": True, "bt": True})
        self.assertEqual(plan["owned"], set())
        self.assertEqual(len(plan["notes"]), 2)
        self.assertIn("USB rail is off", plan["notes"][0])

    def test_a_scan_of_ours_that_ran_its_full_time_is_started_again(self):
        was = {"wifi": True, "bt": True}
        plan = ev.reconcile(self.usable, {"wifi": True, "bt": False}, {"wifi", "bt"}, was, {"bt": 603.0}, self.limits)
        self.assertEqual(plan["start"], ["bt"])

    def test_a_scan_of_ours_that_was_stopped_early_is_left_off_and_let_go_of(self):
        was = {"wifi": True, "bt": True}
        plan = ev.reconcile(self.usable, {"wifi": True, "bt": False}, {"wifi", "bt"}, was, {"bt": 90.0}, self.limits)
        self.assertEqual(plan["start"], [])
        self.assertEqual(plan["owned"], {"wifi"})
        self.assertIn("stopped outside this session", plan["notes"][0])

    def test_ended_by_itself_needs_a_known_limit(self):
        self.assertTrue(ev.ended_by_itself(585.0, 600))
        self.assertFalse(ev.ended_by_itself(500.0, 600))
        self.assertFalse(ev.ended_by_itself(5000.0, None))

    def test_a_scan_counts_as_a_source_only_while_it_is_running(self):
        active = ev.active_sources(self.usable, {"wifi": False, "bt": True})
        self.assertEqual(active, {"bt", "gps", "mesh", "sdr", "sys"})
        off = ev.availability({})
        self.assertEqual(ev.active_sources(off, {"wifi": False, "bt": False}), {"sys"})

    def test_a_source_joining_or_dropping_out_is_said_once_with_the_reason(self):
        off = ev.availability({"rails": {"GPS": "off"}})
        self.assertEqual(ev.source_changes(None, {"sys", "gps"}, off), [])
        self.assertEqual(ev.source_changes({"sys"}, {"sys", "gps"}, off), ["GPS joined the session"])
        self.assertEqual(ev.source_changes({"sys", "gps"}, {"sys"}, off), ["GPS dropped out: the GPS rail is off"])
        self.assertEqual(ev.source_changes({"sys"}, {"sys"}, off), [])


class SourceRowTests(unittest.TestCase):
    def test_a_source_that_is_off_is_greyed_with_why(self):
        avail = ev.availability({"rails": {"GPS": "off", "USB": "on"}}, bt_adapter=True, air_there=True)
        states = {s["key"]: s for s in ev.source_states(avail, {"wifi": False, "bt": False})}
        self.assertFalse(states["gps"]["on"])
        self.assertEqual(states["gps"]["text"], "the GPS rail is off")
        self.assertTrue(states["wifi"]["on"])
        self.assertIn("Start begins listening", states["wifi"]["text"])
        text = ev.sources_html(list(states.values()), good="#111111", muted="#222222")
        self.assertIn('<span style="color:#222222"><b>GPS</b>: the GPS rail is off</span>', text)
        self.assertIn('<span style="color:#111111"><b>Wi-Fi</b>', text)

    def test_the_row_says_which_scans_the_session_started_and_which_it_will_leave_running(self):
        avail = ev.availability(ALL_ON, **everything_usable())
        states = {s["key"]: s for s in ev.source_states(avail, {"wifi": True, "bt": True}, {"bt"}, True, 433.92)}
        self.assertEqual(states["bt"]["text"], "listening (started by this session)")
        self.assertIn("Stop leaves it running", states["wifi"]["text"])
        self.assertEqual(states["sdr"]["text"], "receiving on 433.920 MHz")

    def test_while_recording_a_usable_scan_that_is_not_running_is_shown_as_not_in(self):
        avail = ev.availability(ALL_ON, **everything_usable())
        states = {s["key"]: s for s in ev.source_states(avail, {"wifi": False, "bt": True}, {"bt"}, True)}
        self.assertFalse(states["wifi"]["on"])
        self.assertEqual(states["wifi"]["text"], "not running")

    def test_names_in_the_row_are_escaped(self):
        self.assertIn("&lt;b&gt;", ev.sources_html([{"key": "x", "label": "<b>", "on": True, "text": "a & b"}]))


class SampleTests(unittest.TestCase):
    """What one reading holds."""

    def test_a_reading_with_a_fix_is_stamped_with_time_and_position(self):
        rec = ev.build_sample(T0, {"gps", "sys"}, tpv=TPV, sky=SKY)
        self.assertEqual((rec["k"], rec["ts"], rec["lat"], rec["lon"]), ("sample", T0, 53.1, -1.2))
        self.assertEqual(rec["gps"], {"mode": 3, "used": 2, "seen": 3, "eph": 4.2, "alt": 203.9, "hdop": 0.9})
        self.assertEqual(rec["t"], ev.stamp(T0))

    def test_a_reading_without_a_fix_has_no_position(self):
        rec = ev.build_sample(T0, {"gps"}, tpv={"class": "TPV", "mode": 1}, sky=SKY)
        self.assertNotIn("lat", rec)
        self.assertEqual(rec["gps"]["mode"], 1)

    def test_gpsd_giving_no_answer_is_said_rather_than_left_out(self):
        self.assertEqual(ev.build_sample(T0, {"gps"})["gps"], {"answer": False})

    def test_a_source_that_is_not_in_the_session_leaves_no_trace_in_the_reading(self):
        rec = ev.build_sample(T0, {"sys"}, wifi={"nets": 4}, bt={"addrs": 9}, tpv=TPV, sky=SKY, mesh_nodes=3,
                              sdr_mhz=433.92, power={"capacity": 80, "power": -3.25}, temp=51.5)
        self.assertEqual(set(rec), {"k", "t", "ts", "sys"})
        self.assertEqual(rec["sys"], {"battery": 80, "watts": -3.25, "temp": 51.5})

    def test_every_source_that_is_in_gets_its_own_small_dict(self):
        u = ev.Uniques()
        rec = ev.build_sample(T0, {"wifi", "bt", "gps", "mesh", "sdr", "sys"},
                              wifi=ev.wifi_counts(["aa:aa:aa:aa:aa:01"], ["aa:aa:aa:aa:aa:02"], u),
                              bt=ev.bt_counts(["AA:AA:AA:AA:AA:03"], u), tpv=TPV, sky=SKY, mesh_nodes=4, sdr_mhz=433.92,
                              power={"capacity": 77}, temp=48.0)
        self.assertEqual(rec["wifi"], {"nets": 1, "devs": 1, "nets_total": 1, "devs_total": 1})
        self.assertEqual(rec["bt"], {"addrs": 1, "addrs_total": 1})
        self.assertEqual((rec["mesh"], rec["sdr"]), ({"nodes": 4}, {"mhz": 433.92}))
        json.dumps(rec)

    def test_a_reading_never_holds_an_address(self):
        u = ev.Uniques()
        rec = ev.build_sample(T0, {"wifi", "bt"}, wifi=ev.wifi_counts(["aa:bb:cc:dd:ee:01"], ["aa:bb:cc:dd:ee:02"], u),
                              bt=ev.bt_counts(["aa:bb:cc:dd:ee:03"], u))
        self.assertNotIn("aa:bb", json.dumps(rec))

    def test_running_totals_count_each_address_once_however_often_it_is_heard(self):
        u = ev.Uniques()
        ev.wifi_counts(["aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02"], [], u)
        again = ev.wifi_counts(["AA:AA:AA:AA:AA:02", "aa:aa:aa:aa:aa:03"], [], u)
        self.assertEqual((again["nets"], again["nets_total"]), (2, 3))
        self.assertEqual(ev.bt_counts([], u), {"addrs": 0, "addrs_total": 0})

    def test_a_device_that_gains_a_name_half_way_through_is_still_one_address(self):
        u = ev.Uniques()
        ev.wifi_counts([], ["aa:aa:aa:aa:aa:09"], u)
        named = ev.wifi_counts([], ["printer  (AA:AA:AA:AA:AA:09)"], u)
        self.assertEqual(named["devs_total"], 1)
        self.assertEqual(ev.mac_of("no address here"), "no address here")

    def test_rail_changes_are_named_and_the_first_look_says_nothing(self):
        self.assertEqual(ev.rail_changes(None, {"GPS": "on"}), [])
        self.assertEqual(ev.rail_changes({"GPS": "off", "LORA": "on"}, {"GPS": "on", "LORA": "on"}), ["GPS rail on"])
        self.assertEqual(ev.rail_changes({"LORA": "on"}, {"LORA": "off"}), ["LoRa rail off"])
        self.assertEqual(ev.rail_changes({}, {"GPS": "on"}), [])

    def test_fix_events_are_put_in_words_and_rail_events_are_left_to_the_status(self):
        self.assertEqual(ev.fix_event_text("gps_first_fix", {"seconds_since_rail_on": 41.6}),
                         "First GPS fix (42 s after the rail came on)")
        self.assertEqual(ev.fix_event_text("gps_fix_lost"), "GPS fix lost")
        self.assertEqual(ev.fix_event_text("gps_fix_back", {}), "GPS fix back")
        self.assertIsNone(ev.fix_event_text("gps_rail_on"))

    def test_only_network_and_security_lines_of_the_activity_log_become_events(self):
        self.assertEqual(ev.activity_event("New network: Cafe (aa:bb:cc:dd:ee:ff), WPA2.")[0], "network")
        self.assertEqual(ev.activity_event("Network security changed: Cafe was WPA2, now open.")[0], "security")
        self.assertEqual(ev.activity_event("Deauthentication burst: 40 frames")[0], "security")
        for other in ("Bluetooth scan started", "shell: ls", "", None):
            self.assertIsNone(ev.activity_event(other))

    def test_an_event_is_one_tidy_line(self):
        e = ev.event(T0, "rail", "GPS   rail\non")
        self.assertEqual((e["k"], e["what"], e["text"], e["ts"]), ("event", "rail", "GPS rail on", T0))


class SessionTests(unittest.TestCase):
    def test_records_are_sorted_into_their_kinds_and_anything_else_is_refused(self):
        s = session_of(sample(5, sys={"battery": 80}), ev.event(T0 + 6, "rail", "GPS rail on"))
        self.assertEqual((len(s.samples), len(s.events)), (1, 1))
        self.assertFalse(s.add({"k": "gps", "ts": T0}))
        self.assertFalse(s.add({"k": "sample"}))
        self.assertFalse(s.add(None))

    def test_a_session_that_was_never_stopped_ends_at_the_last_thing_written(self):
        s = session_of(sample(5), sample(10))
        self.assertEqual((s.t0, s.t1), (T0, T0 + 10))
        s.add(ev.stop_record(T0 + 12, {}))
        self.assertEqual(s.t1, T0 + 12)

    def test_an_empty_session_has_no_span_and_does_not_fall_over(self):
        s = ev.Session()
        self.assertEqual((s.t0, s.t1, s.every), (0.0, 0.0, ev.SAMPLE_EVERY))
        self.assertEqual(ev.summarise(s)["samples"], 0)
        self.assertEqual(ev.lanes(s, 0, 60, 10), [])
        self.assertIsNone(ev.sample_at(s, 5))
        self.assertEqual(ev.changed_together(s), {"rows": [], "pairs": []})

    def test_a_value_is_a_number_or_nothing(self):
        rec = sample(0, wifi={"nets": 4, "odd": "x", "flag": True}, gps={"answer": False})
        self.assertEqual(ev.value(rec, "wifi.nets"), 4)
        for key in ("wifi.odd", "wifi.flag", "bt.addrs", "gps.used"):
            self.assertIsNone(ev.value(rec, key))


class ChartTests(unittest.TestCase):
    """The chart as numbers: each lane on its own range, gaps where a source was not in."""

    def test_each_series_is_scaled_to_its_own_range(self):
        self.assertEqual([ev.normalise(v, 10, 20) for v in (10, 15, 20, None)], [0.0, 0.5, 1.0, None])
        self.assertEqual(ev.normalise(7, 7, 7), 0.5)
        self.assertEqual(ev.span([None, 3, 9, None]), (3, 9))
        self.assertIsNone(ev.span([None]))

    def test_only_series_with_numbers_get_a_lane_and_the_real_range_is_kept_for_the_label(self):
        s = session_of(sample(0, wifi={"nets": 10}, sys={"battery": 80}), sample(5, wifi={"nets": 30}, sys={"battery": 79}))
        rows = ev.lanes(s, T0, T0 + 60, 12)
        self.assertEqual([r["key"] for r in rows], ["wifi.nets", "sys.battery"])
        self.assertEqual((rows[0]["lo"], rows[0]["hi"], rows[0]["shown"]), (10, 30, 30))
        self.assertEqual(rows[0]["cols"][0], 0.0)
        self.assertEqual(rows[0]["cols"][1], 1.0)

    def test_the_value_printed_follows_the_reading_being_scrubbed(self):
        s = session_of(sample(0, wifi={"nets": 10}), sample(5, wifi={"nets": 30}))
        self.assertEqual(ev.lanes(s, T0, T0 + 60, 12, at=s.samples[0])[0]["shown"], 10)

    def test_a_reading_is_held_until_the_next_one(self):
        cols = ev.to_columns([(0.0, 1), (10.0, 2)], 0.0, 20.0, 20, hold=12.5)
        self.assertEqual(cols[:10], [1] * 10)
        self.assertEqual(cols[10], 2)

    def test_a_source_that_went_quiet_leaves_a_gap_not_a_flat_line(self):
        cols = ev.to_columns([(0.0, 1), (5.0, None), (10.0, None), (40.0, 2)], 0.0, 50.0, 50, hold=12.5)
        self.assertEqual(cols[4], 1)
        self.assertIsNone(cols[5])
        self.assertIsNone(cols[39])
        self.assertEqual(cols[40], 2)
        late = ev.to_columns([(0.0, 1), (40.0, 2)], 0.0, 50.0, 50, hold=12.5)
        self.assertEqual((late[11], late[12]), (1, None))

    def test_readings_outside_the_view_are_not_drawn_and_a_degenerate_view_is_empty(self):
        self.assertEqual(ev.to_columns([(0.0, 1), (100.0, 2)], 50.0, 60.0, 5, hold=5.0), [None] * 5)
        self.assertEqual(ev.to_columns([(0.0, 1)], 10.0, 10.0, 5, hold=5.0), [None] * 5)
        self.assertEqual(ev.to_columns([(0.0, 1)], 0.0, 10.0, 0, hold=5.0), [])

    def test_the_view_is_never_shorter_than_a_minute_and_leaves_room_for_the_newest_reading(self):
        s = session_of(sample(0), sample(5))
        self.assertEqual(ev.view(s), (T0, T0 + 60))
        long = session_of(*[sample(i * 5.0) for i in range(200)])
        self.assertEqual(ev.view(long), (T0, T0 + 1000))
        self.assertEqual(ev.view(long, 300), (T0 + 700, T0 + 1000))
        self.assertEqual(ev.view(s, 300), (T0, T0 + 60))

    def test_events_become_one_mark_a_column(self):
        s = session_of(ev.event(T0 + 1, "rail", "GPS rail on"), ev.event(T0 + 2, "fix", "First GPS fix"),
                       ev.event(T0 + 30, "source", "GPS joined the session"), ev.event(T0 + 500, "rail", "later"))
        got = ev.marks(s, T0, T0 + 60, 12)
        self.assertEqual(got, [(0, "rail", ["GPS rail on", "First GPS fix"]), (6, "source", ["GPS joined the session"])])
        self.assertEqual(ev.marks(s, T0, T0, 12), [])

    def test_the_time_axis_is_labelled_with_clock_times(self):
        ticks = ev.time_ticks(T0, T0 + 60)
        self.assertEqual([p for p, _w in ticks], [0.0, 0.25, 0.5, 0.75, 1.0])
        self.assertEqual(len(ticks[0][1]), 8)
        self.assertEqual(len(ev.time_ticks(T0, T0 + 7200)[0][1]), 5)
        self.assertEqual(ev.time_ticks(T0, T0), [])

    def test_scrubbing_finds_the_reading_in_force_and_the_keys_step_one_reading(self):
        s = session_of(sample(0), sample(5), sample(10))
        self.assertEqual(ev.sample_at(s, T0 + 7)["ts"], T0 + 5)
        self.assertEqual(ev.sample_at(s, T0 - 50)["ts"], T0)
        self.assertEqual(ev.sample_at(s, T0 + 500)["ts"], T0 + 10)
        self.assertEqual(ev.step_sample(s, T0 + 7, 1), T0 + 10)
        self.assertEqual(ev.step_sample(s, T0 + 7, -1), T0)
        self.assertEqual(ev.step_sample(s, T0 + 10, 1), T0 + 10)
        self.assertEqual(ev.step_sample(s, T0, -1), T0)

    def test_the_readout_says_what_one_reading_holds_in_real_units(self):
        rec = sample(0, lat=53.1, lon=-1.2, wifi={"nets": 12}, gps={"mode": 3, "used": 9, "eph": 4.25, "alt": 35.0},
                     sdr={"mhz": 433.92}, sys={"battery": 81, "temp": 52.04})
        text = ev.readout(rec)
        for part in ("Wi-Fi networks 12", "GPS satellites used 9", "GPS accuracy 4.2 m", "SDR frequency 433.920 MHz",
                     "Battery 81%", "Temperature 52.0 °C", "at 53.10000, -1.20000"):
            self.assertIn(part, text)
        self.assertIn("no GPS fix", ev.readout(sample(0, gps={"mode": 1})))
        self.assertIn("GPS gave no answer", ev.readout(sample(0, gps={"answer": False})))
        self.assertEqual(ev.readout(None), "")
        self.assertEqual(ev.fmt("wifi.nets", None), "-")


class SummaryTests(unittest.TestCase):
    def walk(self):
        """Ten minutes walking north at about a metre a second, networks thinning out, Bluetooth getting busier."""
        recs = []
        for i in range(120):
            at = i * 5.0
            recs.append(sample(at, lat=round(53.0 + i * 0.000045, 6), lon=-1.2,
                               wifi={"nets": 40 - i // 6, "devs": 10, "nets_total": 40 + i // 12, "devs_total": 10},
                               bt={"addrs": 5 + i // 4, "addrs_total": 5 + i // 2}, gps={"mode": 3, "used": 9, "eph": 4.0},
                               sys={"battery": 80 - i // 40, "temp": 50.0}))
        return session_of(*recs)

    def test_the_summary_gives_duration_distance_and_the_range_of_each_series(self):
        s = self.walk()
        s.add(ev.event(T0 + 30, "rail", "GPS rail on"))
        out = ev.summarise(s)
        self.assertEqual((out["seconds"], out["samples"], out["fix_samples"], out["events"]), (595.0, 120, 120, 1))
        self.assertAlmostEqual(out["distance_m"], 595.0, delta=15.0)
        self.assertEqual(out["series"]["wifi.nets"], {"min": 21, "peak": 40, "last": 21, "readings": 120})
        self.assertEqual(out["totals"], {"wifi.nets_total": 49, "wifi.devs_total": 10, "bt.addrs_total": 64})
        self.assertNotIn("mesh.nodes", out["series"])
        json.dumps(out)

    def test_standing_still_is_not_a_walk(self):
        wobble = [sample(i * 5.0, lat=53.0 + (i % 2) * 0.00001, lon=-1.2) for i in range(100)]
        self.assertEqual(ev.distance_m(wobble), 0.0)
        self.assertIsNone(ev.distance_m([sample(0, lat=53.0, lon=-1.2)]))
        self.assertIsNone(ev.distance_m([sample(0), sample(5)]))

    def test_the_summary_in_words_says_what_the_totals_are_counts_of(self):
        s = self.walk()
        s.add(ev.event(T0 + 30, "rail", "GPS rail on"))
        lines = ev.summary_lines(ev.summarise(s), s.events)
        text = "\n".join(lines)
        self.assertIn("ran 9 min 55 s, 120 readings", lines[0])
        self.assertIn("GPS fix in 120 of 120 readings. Moved about", text)
        self.assertIn("Wi-Fi networks: 21 to 40, last 21", text)
        self.assertIn("Different Bluetooth addresses heard: 64", text)
        self.assertIn("addresses, not devices or people", text)
        self.assertIn("Events: 1", text)
        self.assertIn("GPS rail on", lines[-1])

    def test_a_session_with_no_fix_says_nothing_has_a_position(self):
        s = session_of(sample(0, sys={"battery": 80}), sample(5, sys={"battery": 80}))
        text = "\n".join(ev.summary_lines(ev.summarise(s)))
        self.assertIn("No GPS fix during the session", text)
        self.assertIn("Events: none", text)
        self.assertNotIn("not devices", text)

    def test_durations_read_naturally(self):
        self.assertEqual([ev.duration_text(s) for s in (0, 59, 61, 3725, -4)],
                         ["0 s", "59 s", "1 min 01 s", "1 h 02 min", "0 s"])

    def test_the_field_log_line_is_flat_and_small(self):
        flat = ev.compact_summary(ev.summarise(self.walk()))
        self.assertEqual(flat["wifi_nets_peak"], 40)
        self.assertEqual(flat["bt_addrs_total"], 64)
        self.assertTrue(all(not isinstance(v, (dict, list)) for v in flat.values()))


class TogetherTests(unittest.TestCase):
    """'What changed together' is two counts changing in the same minute, and is worded as only that."""

    def minutes(self, wifi, bt, events=()):
        recs = []
        for minute, (w, b) in enumerate(zip(wifi, bt)):
            for step in range(12):
                fields = {}
                if w is not None:
                    fields["wifi"] = {"nets": w}
                if b is not None:
                    fields["bt"] = {"addrs": b}
                recs.append(sample(minute * 60 + step * 5.0, **fields))
        s = session_of(*recs)
        for e in events:
            s.add(e)
        return s

    def test_a_real_change_is_at_least_one_whole_thing_and_a_tenth(self):
        self.assertEqual([ev.direction(a, b) for a, b in ((40, 41), (40, 44), (40, 36), (2, 3), (0, 0.5), (5, 5))],
                         [0, 1, -1, 1, 0, 0])

    def test_readings_are_averaged_into_minutes(self):
        s = session_of(sample(0, wifi={"nets": 10}), sample(30, wifi={"nets": 20}, lat=53.0, lon=-1.2),
                       sample(130, wifi={"nets": 7}))
        got = ev.bucket_means(s)
        self.assertEqual([b["i"] for b in got], [0, 2])
        self.assertEqual(got[0]["means"], {"wifi.nets": 15.0})
        self.assertEqual((got[0]["lat"], got[1]["lat"]), (53.0, None))

    def test_minutes_where_both_counts_moved_are_listed_with_their_figures(self):
        s = self.minutes([10, 10, 10, 20, 20, 10], [5, 5, 5, 9, 9, 9])
        out = ev.changed_together(s)
        self.assertEqual(len(out["rows"]), 1)
        row = out["rows"][0]
        self.assertEqual((row["a"], row["b"], row["a_from"], row["a_to"], row["b_from"], row["b_to"], row["same"]),
                         ("wifi.nets", "bt.addrs", 10.0, 20.0, 5.0, 9.0, True))
        self.assertEqual(row["t"], T0 + 180)
        self.assertEqual(out["pairs"], [{"a": "wifi.nets", "b": "bt.addrs", "minutes": 4, "same": 1, "opposite": 0}])

    def test_counts_moving_opposite_ways_are_tallied_as_that(self):
        out = ev.changed_together(self.minutes([10, 10, 10, 20], [9, 9, 9, 5]))
        self.assertFalse(out["rows"][0]["same"])
        self.assertEqual((out["pairs"][0]["same"], out["pairs"][0]["opposite"]), (0, 1))

    def test_one_count_changing_alone_is_not_listed(self):
        out = ev.changed_together(self.minutes([10, 10, 10, 20, 30], [5, 5, 5, 5, 5]))
        self.assertEqual(out["rows"], [])
        self.assertEqual(out["pairs"][0]["minutes"], 3)

    def test_two_counts_from_the_same_source_are_not_compared_with_each_other(self):
        recs = [sample(m * 60 + i * 5.0, wifi={"nets": 10 * (m + 1), "devs": 10 * (m + 1)}) for m in range(5) for i in range(12)]
        self.assertEqual(ev.changed_together(session_of(*recs)), {"rows": [], "pairs": []})

    def test_the_minutes_after_a_scan_starts_are_flagged_and_kept_out_of_the_tally(self):
        started = [ev.event(T0, "source", "Bluetooth scan started by this session")]
        out = ev.changed_together(self.minutes([2, 10, 20, 20, 20], [1, 8, 16, 16, 16], started))
        self.assertEqual([r["settling"] for r in out["rows"]], [True, True])
        self.assertEqual(out["pairs"], [{"a": "wifi.nets", "b": "bt.addrs", "minutes": 2, "same": 0, "opposite": 0}])

    def test_a_source_that_has_only_just_appeared_is_settling_too(self):
        out = ev.changed_together(self.minutes([10, 10, 10, 20, 20], [None, None, 4, 9, 9]))
        self.assertEqual([r["settling"] for r in out["rows"]], [True])
        self.assertEqual(out["pairs"][0]["minutes"], 1)

    def test_a_gap_in_the_readings_is_not_compared_across(self):
        recs = [sample(0, wifi={"nets": 10}, bt={"addrs": 5}), sample(300, wifi={"nets": 30}, bt={"addrs": 20})]
        self.assertEqual(ev.changed_together(session_of(*recs)), {"rows": [], "pairs": []})

    def test_how_far_the_uconsole_moved_that_minute_goes_with_the_row(self):
        s = self.minutes([10, 10, 10, 20], [5, 5, 5, 9])
        for rec in s.samples:
            rec["lat"], rec["lon"] = 53.0 + (rec["ts"] - T0) // 60 * 0.0005, -1.2
        row = ev.changed_together(s)["rows"][0]
        self.assertAlmostEqual(row["moved_m"], 56.0, delta=2.0)

    def test_the_list_says_plainly_what_it_does_and_does_not_mean(self):
        started = [ev.event(T0, "source", "Bluetooth scan started by this session")]
        s = self.minutes([2, 10, 10, 10, 20, 20, 10], [1, 8, 8, 8, 12, 12, 12], started)
        lines = ev.together_lines(ev.changed_together(s))
        text = "\n".join(lines)
        self.assertIn("only says two counts changed in the same minute", lines[1])
        self.assertIn("does not say the same devices", lines[1])
        self.assertIn("Wi-Fi networks and Bluetooth addresses: in 4 minutes compared, both changed in 1 "
                      "(the same way in 1, opposite ways in 0).", text)
        self.assertIn("Wi-Fi networks rose 10 to 20, Bluetooth addresses rose 8 to 12", text)
        self.assertIn("a scan had just started or stopped, so not counted above", text)
        for claim in ("same device", "person", "people", "direction", "because", "caused", "follow"):
            self.assertNotIn(claim, text.replace("does not say the same devices", ""))

    def test_too_little_to_compare_is_said(self):
        self.assertIn("Not enough yet", ev.together_lines(ev.changed_together(ev.Session()))[-1])
        quiet = ev.together_lines(ev.changed_together(self.minutes([10] * 5, [5] * 5)))
        self.assertIn("No minute where two counts both changed", quiet[-1])

    def test_the_newest_events_are_shown_while_recording(self):
        s = session_of()
        self.assertIn("Events appear here", ev.live_lines(s)[0])
        for i in range(12):
            s.add(ev.event(T0 + i, "rail", f"event {i}"))
        lines = ev.live_lines(s)
        self.assertEqual(len(lines), 8)
        self.assertTrue(lines[-1].endswith("event 11"))


class FileTests(unittest.TestCase):
    """The session file: private, written as it goes, readable after a flat battery, and cleared with the logs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patch = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def write_session(self, now=T0):
        w = ev.Writer(now)
        w.write(ev.start_record(now, ev.source_states(ev.availability(ALL_ON), {"wifi": False, "bt": False})))
        w.write(ev.build_sample(now + 5, {"gps", "sys"}, tpv=TPV, sky=SKY, power={"capacity": 80}), sync=True)
        w.write(ev.event(now + 6, "rail", "LoRa rail on"))
        w.write(ev.build_sample(now + 10, {"gps", "sys"}, tpv=TPV, sky=SKY, power={"capacity": 80}), sync=True)
        return w

    def test_the_file_is_private_named_by_its_start_time_and_kept_beside_the_field_log(self):
        w = self.write_session()
        self.assertEqual(os.path.dirname(w.path), reports.state_dir())
        self.assertRegex(os.path.basename(w.path), r"^session-\d{8}-\d{6}\.jsonl$")
        self.assertEqual(stat.S_IMODE(os.stat(w.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(reports.state_dir()).st_mode), 0o700)
        w.close()

    def test_every_line_is_on_disk_as_soon_as_it_is_written(self):
        w = self.write_session()
        with open(w.path) as f:                                   # read while the session is still open
            lines = [json.loads(line) for line in f]
        self.assertEqual([r["k"] for r in lines], ["start", "sample", "event", "sample"])
        self.assertEqual(lines[0]["sources"]["gps"], {"on": True, "text": "fix, satellites, accuracy and altitude"})
        self.assertEqual((lines[0]["v"], lines[0]["every"]), (1, 5.0))
        w.close()

    def test_two_sessions_started_in_the_same_second_get_their_own_files(self):
        a, b = ev.Writer(T0), ev.Writer(T0)
        self.assertNotEqual(a.path, b.path)
        self.assertEqual(len(ev.files()), 2)
        a.close()
        b.close()

    def test_writing_after_the_session_is_closed_does_nothing_and_does_not_raise(self):
        w = self.write_session()
        w.close()
        self.assertFalse(w.write(ev.event(T0 + 20, "rail", "late")))
        w.close()
        self.assertEqual(len(ev.load(w.path).events), 1)

    def test_a_full_file_drops_readings_says_so_once_and_still_takes_the_end(self):
        w = ev.Writer(T0, cap=200)
        for i in range(6):
            w.write(ev.build_sample(T0 + i * 5, {"sys"}, power={"capacity": 80}, temp=50.0))
        w.write(ev.stop_record(T0 + 40, {"samples": 3}))
        w.close()
        s = ev.load(w.path)
        self.assertEqual(len(s.samples), 3)
        self.assertEqual([e["text"] for e in s.events], ["The session file is full: nothing more is recorded"])
        self.assertIsNotNone(s.stop)

    def test_a_session_reads_back_as_it_was_written(self):
        w = self.write_session()
        w.write(ev.stop_record(T0 + 12, ev.summarise(ev.load(w.path), T0 + 12)))
        w.close()
        s = ev.load(w.path)
        self.assertEqual((len(s.samples), len(s.events), s.t0, s.t1, s.path), (2, 1, T0, T0 + 12, w.path))
        self.assertEqual(s.stop["summary"]["samples"], 2)
        self.assertEqual(s.samples[0]["lat"], 53.1)

    def test_a_session_cut_short_by_a_flat_battery_still_reads_back(self):
        w = self.write_session()
        w.close()
        with open(w.path, "a") as f:
            f.write('{"k":"sample","t":"2026-11-25T10:00:15","ts":17950000')      # the line it was half way through
        s = ev.load(w.path)
        self.assertEqual(len(s.samples), 2)
        self.assertIsNone(s.stop)
        self.assertEqual(s.t1, T0 + 10)
        self.assertEqual(ev.load(os.path.join(self.tmp.name, "nope.jsonl")).samples, [])

    def test_saved_sessions_are_listed_oldest_first_and_other_files_are_ignored(self):
        first, second = ev.Writer(T0), ev.Writer(T0 + 3600)
        first.close()
        second.close()
        for other in ("field-20261125.jsonl", "ops_gui.log", "session-notes.jsonl", "session-20261125-100000.jsonl.bak"):
            open(os.path.join(reports.state_dir(), other), "w").close()
        self.assertEqual(ev.files(), [first.path, second.path])
        self.assertEqual(ev.files(os.path.join(self.tmp.name, "nowhere")), [])

    def test_the_picker_names_a_session_by_when_it_started(self):
        label = ev.picker_label("/x/session-20261125-140310.jsonl", 84 * 1024)
        self.assertEqual(label, "25 Nov 2026 14:03:10  (84 KB)")
        self.assertEqual(ev.picker_label("/x/odd.jsonl", 10), "odd.jsonl  (1 KB)")

    def test_the_csv_has_a_row_a_reading_and_the_events_beside_the_reading_they_came_before(self):
        w = self.write_session()
        w.close()
        rows = ev.csv_text(ev.load(w.path)).splitlines()
        self.assertEqual(rows[0].split(",")[:6], ["time", "epoch", "lat", "lon", "wifi.nets", "wifi.devs"])
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[1].endswith(","))
        self.assertTrue(rows[2].endswith(",LoRa rail on"))
        self.assertIn(",53.1,-1.2,", rows[1])
        only_events = session_of(ev.event(T0 + 1, "rail", "GPS rail on"))
        self.assertTrue(ev.csv_text(only_events).splitlines()[1].endswith(",GPS rail on"))

    def test_export_writes_a_private_csv_next_to_the_session(self):
        w = self.write_session()
        w.close()
        path = ev.export_csv(ev.load(w.path))
        self.assertEqual(path, w.path[:-len(".jsonl")] + ".csv")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(ev.export_csv(ev.load(w.path)), path)            # exporting twice just writes it again
        self.assertEqual(ev.files(), [w.path])
        self.assertEqual(ev.all_files(), sorted([w.path, path]))

    def test_delete_removes_the_session_and_its_csv_and_nothing_else(self):
        w = self.write_session()
        w.close()
        ev.export_csv(ev.load(w.path))
        log = os.path.join(reports.state_dir(), "ops_gui.log")
        open(log, "w").close()
        self.assertEqual(ev.delete(log), 0)
        self.assertEqual(ev.delete(None), 0)
        self.assertTrue(os.path.exists(log))
        self.assertEqual(ev.delete(w.path), 2)
        self.assertEqual(ev.all_files(), [])

    def test_clear_removes_every_session_and_its_csv_with_the_other_logs(self):
        w = self.write_session()
        w.close()
        ev.export_csv(ev.load(w.path))
        reports.write_log("x", True)
        self.assertEqual(reports.clear_all(), 3)
        self.assertEqual(ev.all_files(), [])

    def test_a_session_never_goes_into_a_problem_report(self):
        w = self.write_session()
        w.close()
        reports.write_log("the app log line", True)
        with mock.patch.object(reports, "_run", return_value="ok"):
            text = reports.build_report(["Home", "Everything"])
        self.assertIn("the app log line", text)
        for private in ("53.1", "LoRa rail on", '"k":"sample"', "session-"):
            self.assertNotIn(private, text)


class AircraftTests(unittest.TestCase):
    """The aircraft lane: two counts read from the file a receiver that is already running writes."""

    def doc(self, *aircraft, now=T0):
        return json.dumps({"now": now, "messages": 1234, "aircraft": list(aircraft)})

    def test_aircraft_heard_in_the_last_minute_are_counted_and_how_many_give_a_position(self):
        text = self.doc({"hex": "4ca123", "seen": 0.4, "lat": 53.2, "lon": -1.1, "seen_pos": 1.2},
                        {"hex": "4ca124", "seen": 12.0},
                        {"hex": "4ca125", "seen": 59.0, "lat": 53.0, "lon": -1.0},
                        {"hex": "4ca126", "seen": 250.0, "lat": 53.0, "lon": -1.0, "seen_pos": 250.0})
        self.assertEqual(ev.aircraft_counts(text, T0 + 1), {"heard": 3, "pos": 2})

    def test_a_position_that_has_gone_old_is_not_counted_as_a_position(self):
        text = self.doc({"hex": "a", "seen": 1.0, "lat": 53.2, "lon": -1.1, "seen_pos": 200.0})
        self.assertEqual(ev.aircraft_counts(text, T0), {"heard": 1, "pos": 0})

    def test_a_quiet_sky_is_a_count_of_none_not_a_missing_receiver(self):
        self.assertEqual(ev.aircraft_counts(self.doc(), T0), {"heard": 0, "pos": 0})

    def test_a_file_left_behind_by_a_receiver_that_stopped_is_not_a_reading(self):
        text = self.doc({"hex": "a", "seen": 1.0})
        self.assertIsNone(ev.aircraft_counts(text, T0 + 31))
        self.assertIsNotNone(ev.aircraft_counts(text, T0 + 29))

    def test_a_damaged_file_is_no_reading_and_never_an_error(self):
        for text in (None, "", "{", "[]", "null", '{"now": 1}', '{"aircraft": []}', '{"now": "soon", "aircraft": []}',
                     '{"now": NaN, "aircraft": []}', json.dumps({"now": T0, "aircraft": {"hex": "a"}}), "[" * 100000,
                     self.doc()[:-9]):
            self.assertIsNone(ev.aircraft_counts(text, T0), text if text is None else text[:40])

    def test_entries_that_make_no_sense_are_not_counted(self):
        text = self.doc(None, "x", 7, {}, {"seen": "now"}, {"seen": -5}, {"seen": True}, {"seen": float("inf")},
                        {"seen": 1.0, "lat": "53", "lon": -1.0}, {"seen": 2.0, "lat": 53.0}, {"seen": 3.0, "lat": 53.0, "lon": -1.0,
                                                                                         "seen_pos": "x"})
        self.assertEqual(ev.aircraft_counts(text, T0), {"heard": 3, "pos": 0})

    def test_the_file_is_only_read_and_a_missing_one_means_the_receiver_is_not_running(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "aircraft.json")
            self.assertIsNone(ev.read_aircraft(path, T0))
            with open(path, "w") as f:
                f.write(self.doc({"hex": "a", "seen": 1.0}))
            self.assertEqual(ev.read_aircraft(path, T0), {"heard": 1, "pos": 0})
            self.assertEqual(os.listdir(d), ["aircraft.json"])
            self.assertIsNone(ev.read_aircraft(d, T0))                    # not a file at all
        self.assertEqual(ev.AIRCRAFT_FILE, "/run/readsb/aircraft.json")

    def test_the_lane_is_greyed_with_the_reason_like_any_other_source(self):
        off = ev.availability({"rails": {"SDR": "off"}}, aircraft={"heard": 2, "pos": 1})
        self.assertEqual(off["adsb"], (False, "the SDR rail is off"))
        self.assertEqual(ev.availability(ALL_ON, **everything_usable())["adsb"], (False, "the aircraft receiver is not running"))
        on = ev.availability(ALL_ON, **everything_usable(aircraft={"heard": 0, "pos": 0}))
        self.assertEqual(on["adsb"], (True, ""))
        states = {s["key"]: s for s in ev.source_states(on, {})}
        self.assertEqual((states["adsb"]["label"], states["adsb"]["on"]), ("Aircraft", True))
        greyed = {s["key"]: s for s in ev.source_states(off, {})}
        self.assertEqual((greyed["adsb"]["on"], greyed["adsb"]["text"]), (False, "the SDR rail is off"))
        self.assertIn("adsb", ev.active_sources(on, {}))
        self.assertNotIn("adsb", ev.active_sources(off, {}))
        self.assertEqual(ev.source_changes({"sys", "adsb"}, {"sys"}, off), ["Aircraft dropped out: the SDR rail is off"])

    def test_a_reading_holds_the_two_counts_and_nothing_about_any_aircraft(self):
        rec = ev.build_sample(T0, {"sys", "adsb"}, aircraft={"heard": 3, "pos": 2, "hex": "4ca123"})
        self.assertEqual(rec["adsb"], {"heard": 3, "pos": 2})
        self.assertNotIn("adsb", ev.build_sample(T0, {"sys"}, aircraft={"heard": 3, "pos": 2}))
        self.assertNotIn("adsb", ev.build_sample(T0, {"sys", "adsb"}))
        s = session_of(sample(0, adsb={"heard": 3, "pos": 2}), sample(5, adsb={"heard": 5, "pos": 2}))
        self.assertEqual([lane["label"] for lane in ev.lanes(s, T0, T0 + 60, 10)], ["Aircraft heard", "Aircraft giving a position"])
        self.assertIn("Aircraft heard 5", ev.readout(s.samples[-1]))
        self.assertIn("Aircraft heard: 3 to 5, last 5", ev.summary_lines(ev.summarise(s)))


class BookmarkTests(unittest.TestCase):
    """A bookmark: the last few minutes the tab already holds, kept as a small private session of its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patch = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def readings(self, count, every=5.0):
        return [sample(i * every, sys={"battery": 80 - i}) for i in range(count)]

    def test_the_window_keeps_the_last_five_minutes_with_both_ends_included(self):
        recs = self.readings(100)                                   # 0 to 495 seconds
        got = ev.last_window(recs, 300, T0 + 495)
        self.assertEqual((got[0]["ts"], got[-1]["ts"], len(got)), (T0 + 195, T0 + 495, 61))
        just = ev.last_window(recs, 300, T0 + 495.5)
        self.assertEqual(just[0]["ts"], T0 + 200)                   # 195 is half a second outside now

    def test_fewer_readings_than_the_window_keeps_them_all(self):
        recs = self.readings(6)
        self.assertEqual(ev.last_window(recs, 300, T0 + 25), recs)
        self.assertEqual(ev.last_window(recs, 300), recs)           # no moment given: up to the last reading

    def test_no_readings_gives_nothing_and_nothing_is_saved(self):
        self.assertEqual(ev.last_window([], 300, T0), [])
        self.assertEqual(ev.last_window(None, 300, T0), [])
        self.assertIsNone(ev.bookmark_records(session_of(), [], T0))
        self.assertIsNone(ev.save_bookmark(None, T0))
        self.assertEqual(ev.all_files(), [])

    def test_readings_out_of_order_come_back_in_order_and_later_ones_are_left_out(self):
        recs = self.readings(10)
        shuffled = [recs[i] for i in (7, 2, 9, 0, 5, 3, 8, 1, 6, 4)] + [{"k": "sample", "ts": "late"}]
        got = ev.last_window(shuffled, 20, T0 + 35)
        self.assertEqual([r["ts"] - T0 for r in got], [15, 20, 25, 30, 35])

    def test_the_end_is_now_while_recording_else_the_picked_moment_else_the_last_reading(self):
        recs = self.readings(4)
        self.assertEqual(ev.bookmark_until(True, T0 + 5, recs, T0 + 99), T0 + 99)
        self.assertEqual(ev.bookmark_until(False, T0 + 5, recs, T0 + 99), T0 + 5)
        self.assertEqual(ev.bookmark_until(False, None, recs, T0 + 99), T0 + 15)

    def test_the_file_is_a_session_that_opens_as_one_with_its_note(self):
        recs = self.readings(80)
        s = session_of(*recs)
        s.path = "/x/session-20261125-140310.jsonl"
        s.add(ev.event(T0 + 50, "rail", "too early"))
        s.add(ev.event(T0 + 300, "rail", "LoRa rail on"))
        picked = ev.last_window(s.samples, 300, T0 + 395)
        records = ev.bookmark_records(s, picked, T0 + 400, "  heard   it\nhere  ")
        self.assertEqual([r["k"] for r in records][:2] + [r["k"] for r in records][-2:], ["start", "sample", "event", "stop"])
        self.assertEqual(records[0]["bookmark"], {"made": ev.stamp(T0 + 400), "seconds": 300, "note": "heard it here",
                                                  "from": "session-20261125-140310.jsonl"})
        path = ev.save_bookmark(records, T0 + 400)
        self.assertRegex(os.path.basename(path), r"^session-\d{8}-\d{6}-bookmark\.jsonl$")
        self.assertEqual(os.path.dirname(path), reports.state_dir())
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(reports.state_dir()).st_mode), 0o700)
        self.assertEqual(ev.files(), [path])
        back = ev.load(path)
        self.assertEqual((len(back.samples), back.t0, back.t1), (61, T0 + 95, T0 + 395))
        self.assertEqual([e["text"] for e in back.events], ["LoRa rail on"])
        self.assertEqual(back.stop["summary"]["samples"], 61)
        self.assertEqual(ev.bookmark_note(back), "heard it here")
        self.assertIsNone(ev.bookmark_note(s))
        self.assertTrue(ev.picker_label(path).startswith("Bookmark "))
        self.assertLess(os.path.getsize(path), 20 * 1024)

    def test_two_bookmarks_in_the_same_second_get_their_own_files_and_delete_takes_only_one(self):
        records = ev.bookmark_records(session_of(), self.readings(3), T0)
        a, b = ev.save_bookmark(records, T0), ev.save_bookmark(records, T0)
        self.assertNotEqual(a, b)
        self.assertTrue(b.endswith("-bookmark-2.jsonl"))
        self.assertEqual(ev.files(), sorted([a, b]))
        self.assertEqual(ev.delete(b), 1)
        self.assertEqual(ev.files(), [a])

    def test_a_long_note_is_cut_and_an_empty_one_is_kept_empty(self):
        records = ev.bookmark_records(session_of(), self.readings(2), T0, "x" * 2000)
        self.assertEqual(len(records[0]["bookmark"]["note"]), ev.NOTE_MAX)
        self.assertEqual(ev.bookmark_records(session_of(), self.readings(2), T0)[0]["bookmark"]["note"], "")

    def test_the_picker_names_a_bookmark_as_one(self):
        self.assertEqual(ev.picker_label("/x/session-20261125-140310-bookmark-2.jsonl", 9 * 1024),
                         "Bookmark 25 Nov 2026 14:03:10  (9 KB)")


class TabTests(unittest.TestCase):
    def test_the_tab_sits_after_overview_and_is_there_whatever_the_rails_are_doing(self):
        self.assertEqual(data.tab_names({}, False, home=True, everything=True)[:4], ["Home", "Overview", "Everything", "Wi-Fi"])
        self.assertEqual(data.tab_names(None, False, everything=True)[:2], ["Overview", "Everything"])
        self.assertNotIn("Everything", data.tab_names({}, False))
        self.assertTrue(data.tab_icon_path("Everything", 32))


class PassiveTests(unittest.TestCase):
    """The module is held to what it promises: it listens and counts, and never switches or sends anything."""

    def setUp(self):
        with open(ev.__file__) as f:
            self.source = f.read()
        self.tree = ast.parse(self.source)

    def code_names(self):
        """Every name and attribute the code uses (comments and words in strings are not code)."""
        names = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        return names

    def test_it_never_runs_a_command_or_opens_a_connection_itself(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported & {"subprocess", "socket", "urllib", "http", "requests", "shlex"}, set())
        self.assertNotIn("system", self.code_names())

    def test_it_never_switches_a_rail_or_starts_a_service(self):
        names = self.code_names()
        for forbidden in ("set_rail", "run_privileged", "toggle_rail", "rail_toggle", "acquire", "start_engine",
                          "sdr_on", "gps_on", "lora_on", "systemctl", "connect_engine"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_it_never_sends_or_injects_anything(self):
        names = self.code_names()
        for forbidden in ("send", "sendall", "sendText", "sendto", "deauth", "inject", "probe", "handshake", "pair"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_qt_is_only_imported_inside_build_page(self):
        for node in self.tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.assertNotIn("PyQt6", ast.dump(node))
        self.assertIn("def build_page(win):\n    from PyQt6", self.source)


if __name__ == "__main__":
    unittest.main()
