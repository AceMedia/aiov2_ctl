"""
The Ghost hunt tab's pure parts: the switch, the four meters and their honest captions, the change flares, and
sessions (built, written privately, read back and replayed). No display, no QApplication, no radio.

    python3 -m unittest discover -s tests
"""
import json
import os
import stat
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import aiov2_ctl as a  # noqa: E402
from ops_gui import data, ghost, screensaver  # noqa: E402

BANNED = ("detected", "confirmed", "proof")


def heard(wifi=(), bt=(), mesh=(), gps=None):
    """Plain heard data as read_heard() gives it: lists of {"id", strength}."""
    return {"wifi": [{"id": f"aa:bb:cc:dd:ee:{i:02x}", "rssi": v} for i, v in enumerate(wifi)],
            "bt": [{"id": f"11:22:33:44:55:{i:02x}", "rssi": v} for i, v in enumerate(bt)],
            "mesh": [{"id": f"!node{i}", "snr": v} for i, v in enumerate(mesh)],
            "gps": gps}


def history_of(*readings, every=1.0):
    h = []
    for i, r in enumerate(readings):
        ghost.remember(h, ghost.make_frame(r, 1000.0 + i * every))
    return h


class SwitchTests(unittest.TestCase):
    def test_it_is_on_only_when_the_installer_wrote_the_switch(self):
        self.assertTrue(ghost.enabled("/u", isfile=lambda p: p == "/u/enabled"))
        self.assertFalse(ghost.enabled("/u", isfile=lambda p: False))
        self.assertTrue(ghost.wanted("/u", isfile=lambda p: p == "/u/enabled"))
        self.assertFalse(ghost.wanted("/u", isfile=lambda p: False))

    def test_the_app_and_the_installer_agree_on_the_folder(self):
        self.assertEqual(ghost.user_folder("/home/u"), a.ghost_home("/home/u"))
        self.assertEqual(ghost.sessions_folder("/home/u"), "/home/u/.local/share/aiov2_ctl/ghost/sessions")

    def test_the_tab_is_only_there_when_it_is_on_and_comes_last(self):
        self.assertNotIn(ghost.TAB, data.tab_names({}, False, wiki=True))
        self.assertEqual(data.tab_names({}, False, wiki=True, ghost=True)[-3:], ["Help", "Wiki", ghost.TAB])
        self.assertIn(ghost.TAB, data.TAB_ICONS)

    def test_the_for_fun_line_is_word_for_word(self):
        self.assertEqual(ghost.FOR_FUN, "For fun. This cannot detect ghosts. Every reading here is a real radio "
                                        "or system measurement, named underneath.")


class InstrumentTests(unittest.TestCase):
    def test_there_are_four_each_with_a_playful_and_an_honest_caption(self):
        h = history_of(heard(wifi=[-50, -60], bt=[-70], mesh=[5]), heard(wifi=[-54, -60], bt=[-70], mesh=[5]))
        meters = ghost.instruments(h)
        self.assertEqual([m["name"] for m in meters], list(ghost.INSTRUMENTS))
        for m in meters:
            self.assertEqual(set(m), {"name", "value", "says", "really"})
            self.assertTrue(0.0 <= m["value"] <= 1.0)
            self.assertIn(m["says"], ghost.SAYS[m["name"]])
            self.assertTrue(m["really"].startswith("Really: "))

    def test_energy_is_the_mean_change_in_db_over_ten_seconds(self):
        h = history_of(heard(wifi=[-50, -60]), heard(wifi=[-54, -60]))
        m = ghost.energy(h)
        self.assertIn("(2.0 dB)", m["really"])                  # (4 + 0) / 2
        self.assertAlmostEqual(m["value"], round(2.0 / ghost.ENERGY_FULL_DB, 3))
        old = history_of(heard(wifi=[-30]), *[heard(wifi=[-60])] * 12)
        self.assertIn("(0.0 dB)", ghost.energy(old)["really"], "a change older than ten seconds is not counted")

    def test_presences_is_a_count_with_its_breakdown(self):
        h = history_of(heard(wifi=[-50] * 14, bt=[-60] * 3, mesh=[1, 2]))
        m = ghost.presences(h)
        self.assertIn("(19)", m["really"])
        self.assertIn("14 Wi-Fi networks, 3 Bluetooth devices, 2 mesh nodes", m["really"])
        self.assertNotIn("GPS", m["really"], "a source that is not listening is not counted")
        one = ghost.presences(history_of(heard(wifi=[-50], bt=[-60])))
        self.assertIn("1 Wi-Fi network, 1 Bluetooth device", one["really"])

    def test_arrivals_counts_things_that_came_and_went_in_the_last_minute(self):
        first = {"wifi": [{"id": "x1", "rssi": -50}, {"id": "x2", "rssi": -60}], "bt": [], "mesh": [], "gps": None}
        later = {"wifi": [{"id": "x2", "rssi": -60}, {"id": "x3", "rssi": -70}, {"id": "x4", "rssi": -71}],
                 "bt": [], "mesh": [], "gps": None}
        h = history_of(first, later, every=30.0)
        m = ghost.arrivals(h)
        self.assertIn("2 heard now that were not heard 30 s ago, and 1 heard then but not now", m["really"])
        self.assertAlmostEqual(m["value"], 0.3)

    def test_stillness_is_the_time_since_anything_changed(self):
        h = history_of(heard(wifi=[-50]), heard(wifi=[-60]), heard(wifi=[-61]), heard(wifi=[-60]), every=5.0)
        m = ghost.stillness(h)
        self.assertIn("Really: 10 s since", m["really"])         # the 10 dB step, small wobbles after it
        quiet = ghost.stillness(history_of(*[heard(wifi=[-50])] * 4, every=5.0))
        self.assertIn("at least 15 s", quiet["really"])
        self.assertTrue(ghost.changed(ghost.make_frame(heard(wifi=[-50]), 0)["bands"],
                                      ghost.make_frame(heard(wifi=[-50, -50]), 0)["bands"]))

    def test_the_playful_words_follow_the_needle(self):
        self.assertEqual(ghost.says("ENERGY", 0.0), "the air is calm")
        self.assertEqual(ghost.says("ENERGY", 0.6), "the air is restless")
        self.assertEqual(ghost.says("ENERGY", 1.0), "the air is wild")
        self.assertEqual(ghost.says("STILLNESS", 2.0), "deathly still")

    def test_with_nothing_received_each_says_so(self):
        for h in ([], history_of(heard()), history_of({})):
            for m in ghost.instruments(h):
                self.assertEqual(m["value"], 0.0)
                self.assertIn("nothing is being received", m["really"])

    def test_no_wording_claims_anything_was_found(self):
        h = history_of(heard(wifi=[-50, -60], bt=[-70]), heard(wifi=[-40], bt=[-70, -80]))
        words = [ghost.FOR_FUN, ghost.NOTHING_SAYS, ghost.NOTHING_REALLY]
        words += [w for ws in ghost.SAYS.values() for w in ws]
        words += [m["says"] + " " + m["really"] for m in ghost.instruments(h)]
        for text in words:
            for bad in BANNED:
                self.assertNotIn(bad, text.lower(), text)
            for bad in ("metre", "meter away", "direction", "behind you", "someone", "person", "spirit is"):
                self.assertNotIn(bad, text.lower(), text)

    def test_the_sdr_is_never_read(self):
        bands = ghost.make_frame({"sdr": [{"id": "x", "rssi": -20}], **heard(wifi=[-50])}, 0)["bands"]
        self.assertEqual([b["key"] for b in bands], ["wifi", "bt", "mesh", "gps"])
        self.assertNotIn("sdr", ghost.heard_ids({"sdr": [{"id": "x", "rssi": -20}]}))


class FlareTests(unittest.TestCase):
    def test_only_bars_that_jumped_flare(self):
        before = ghost.make_frame(heard(wifi=[-50, -60, -70]), 0)["bands"]
        after = ghost.make_frame(heard(wifi=[-50, -45, -70, -80]), 1)["bands"]
        out = ghost.flares(before, after)
        # strongest first: -45 -50 -70 -80 against -50 -60 -70; 5 dB is under the threshold, 10 dB is over it
        self.assertEqual(out, {"wifi": [1, 3]})
        self.assertEqual(ghost.flares(before, before), {})

    def test_a_flare_fades_after_a_moment(self):
        lit = ghost.keep_flares({}, {"wifi": [1]}, now=10.0)
        self.assertEqual(lit, {("wifi", 1): 10.0 + ghost.FLARE_HOLD})
        self.assertEqual(ghost.keep_flares(lit, {}, now=20.0), {})


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.h = history_of(heard(wifi=[-50.04, -61.26], bt=[-70], mesh=[3.3]), heard(wifi=[-52, -61], bt=[], mesh=[3]))
        self.meters = ghost.instruments(self.h)

    def test_the_header_says_what_it_is_and_that_it_is_entertainment(self):
        head = json.loads(ghost.header_line(1000.0))
        self.assertEqual((head["format"], head["version"]), (ghost.FORMAT, ghost.FORMAT_VERSION))
        self.assertEqual(head["note"], "entertainment: not evidence of anything")

    def test_a_line_holds_counts_rounded_strengths_and_the_meters(self):
        line = json.loads(ghost.session_line(self.h[0], self.meters, 1000.0))
        self.assertEqual(line["elapsed"], 0.0)
        wifi = line["bands"][0]
        self.assertEqual((wifi["source"], wifi["count"], wifi["strengths"]), ("wifi", 2, [-50.0, -61.3]))
        self.assertEqual(line["bands"][3]["state"], "off")
        self.assertEqual([m["name"] for m in line["instruments"]], list(ghost.INSTRUMENTS))
        self.assertNotIn("position", json.dumps(line))
        self.assertNotIn("lat", line)

    def test_no_address_or_id_can_get_into_a_line(self):
        frame = ghost.make_frame(heard(wifi=[-50] * 5, bt=[-60] * 5, mesh=[1]), 1000.0)
        line = ghost.session_line(frame, ghost.instruments([frame]), 1000.0)
        self.assertIsNone(ghost.ADDRESS.search(line))
        for an_id in ("aa:bb:cc", "11:22:33", "!node"):
            self.assertNotIn(an_id, line)
        sly = dict(frame, bands=[dict(b, title="de:ad:be:ef:00:01", caption="de:ad:be:ef:00:01")
                                 for b in frame["bands"]])
        self.assertIsNone(ghost.ADDRESS.search(ghost.session_line(sly, [], 1000.0)))
        bad = [{"name": "ENERGY", "value": 0, "says": "x", "really": "de:ad:be:ef:00:01"}]
        with self.assertRaises(ValueError):
            ghost.session_line(frame, bad, 1000.0)

    def test_a_session_round_trips_and_drives_the_same_bands(self):
        text = "\n".join([ghost.header_line(1000.0)] +
                         [ghost.session_line(f, self.meters, 1000.0) for f in self.h]) + "\n{\"time\": 1"
        head, records = ghost.parse_session(text)
        self.assertEqual(head["format"], ghost.FORMAT)
        self.assertEqual(len(records), 2, "the cut-off last line is left out")
        again = records[0]["bands"]
        self.assertEqual([b["key"] for b in again], ["wifi", "bt", "mesh", "gps"])
        self.assertEqual(ghost.real_values(again[0]), [-50.0, -61.3])
        self.assertEqual(again[3]["state"], "off")
        self.assertEqual(records[1]["bands"][1]["state"], "nothing")
        self.assertEqual(records[0]["instruments"], self.meters)

    def test_a_file_with_the_wrong_header_is_refused(self):
        for text in ("", "not json", json.dumps({"format": "something-else", "version": 1}),
                     json.dumps({"format": ghost.FORMAT, "version": 99, "note": ghost.ENTERTAINMENT}),
                     json.dumps({"format": ghost.FORMAT, "version": 1})):
            with self.assertRaises(ValueError):
                ghost.parse_session(text)

    def test_frame_at_and_stepping(self):
        records = [{"elapsed": e, "time": 1000 + e, "bands": [], "instruments": []} for e in (0.0, 2.0, 4.0)]
        self.assertEqual(ghost.frame_at(records, -1)["elapsed"], 0.0)
        self.assertEqual(ghost.frame_at(records, 3.9)["elapsed"], 2.0)
        self.assertEqual(ghost.frame_at(records, 4.0)["elapsed"], 4.0)
        self.assertEqual(ghost.frame_at(records, 99)["elapsed"], 4.0)
        self.assertIsNone(ghost.frame_at([], 0))
        self.assertEqual(ghost.step(records, 2.0, 1), 4.0)
        self.assertEqual(ghost.step(records, 2.0, -1), 0.0)
        self.assertEqual(ghost.step(records, 4.0, 1), 4.0)
        self.assertEqual(ghost.replay_length(records), 4.0)
        self.assertTrue(ghost.replay_mark({}, records[1]).startswith("REPLAY  "))
        self.assertTrue(ghost.replay_mark({}, records[1]).endswith("+00:02"))

    def test_a_line_every_two_seconds(self):
        self.assertTrue(ghost.due(None, 5.0))
        self.assertFalse(ghost.due(5.0, 6.0))
        self.assertTrue(ghost.due(5.0, 7.0))

    def test_sessions_are_private_files_in_a_private_folder(self):
        with tempfile.TemporaryDirectory() as home:
            folder = ghost.sessions_folder(home)
            path, f = ghost.open_session(folder, 1000.0)
            with f:
                f.write(ghost.header_line(1000.0) + "\n")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(ghost.user_folder(home)).st_mode), 0o700)
            self.assertEqual(ghost.list_sessions(folder), [os.path.basename(path)])
            with open(path) as f:
                self.assertEqual(ghost.parse_session(f.read())[1], [])

    def test_the_list_is_newest_first_and_ignores_other_files(self):
        names = ["ghost-20261101-090000.jsonl", "notes.txt", "ghost-20261128-140322.jsonl"]
        self.assertEqual(ghost.list_sessions("/x", listdir=lambda _: names),
                         ["ghost-20261128-140322.jsonl", "ghost-20261101-090000.jsonl"])
        self.assertEqual(ghost.session_title("ghost-20261128-140322.jsonl"), "28 Nov 2026 14:03:22")
        self.assertEqual(ghost.list_sessions("/no/such/folder"), [])


class SharedReadingTests(unittest.TestCase):
    def test_the_screensaver_and_the_tab_read_the_pages_the_same_way(self):
        class Engine:
            def snapshot(self):
                return {"node_info": {"!a": {"snr": 4.0}}}

        class Win:
            pages = {}
            mesh_hub = type("Hub", (), {"engine": Engine()})()
        out = screensaver.read_heard(Win())
        self.assertEqual(out["mesh"], [{"id": "!a", "snr": 4.0}])
        self.assertIsNone(out["bt"])
        self.assertIsNone(out["wifi"])
        self.assertNotIn("sdr", out)



class TabWordsTests(unittest.TestCase):
    def test_the_buttons_say_what_they_will_do(self):
        self.assertEqual(ghost.button_labels(False, False, False), {"record": "Record", "play": "Play", "live": "Live"})
        self.assertEqual(ghost.button_labels(True, True, True),
                         {"record": "Stop recording", "play": "Pause", "live": "Back to live"})

    def test_the_status_line_says_live_or_replay_and_whether_it_is_recording(self):
        self.assertIn("LIVE", ghost.status_text(False, "", False))
        self.assertIn("nothing is being received", ghost.status_text(False, "", True))
        self.assertIn("on this device only", ghost.status_text(True, "", False))
        replay = ghost.status_text(False, "REPLAY  28 Nov 2026 14:03:22  +00:42", True)
        self.assertTrue(replay.startswith("REPLAY"))
        self.assertNotIn("LIVE", replay)

    def test_a_replayed_file_always_gives_four_sound_meters(self):
        meters = ghost.shown_meters([{"name": "ENERGY", "value": 7, "says": "x", "really": "Really: y"},
                                     {"name": "PRESENCES", "value": True}, "junk"])
        self.assertEqual([m["name"] for m in meters], list(ghost.INSTRUMENTS))
        self.assertEqual(meters[0]["value"], 1.0)
        self.assertEqual(meters[1]["really"], ghost.NOTHING_REALLY)

    def test_the_tab_never_runs_a_command_or_touches_the_sdr_or_a_rail(self):
        with open(ghost.__file__, encoding="utf-8") as f:
            source = f.read()
        for bad in ("subprocess", "os.system", "Popen", "set_rail", "aiov2_ctl.py", "start_scan", "sdr_panel",
                    "requests", "urllib", "socket"):
            self.assertNotIn(bad, source, bad)
        self.assertIn("ss.read_heard(win)", source)

